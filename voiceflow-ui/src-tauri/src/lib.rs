mod paste;
mod window_pos;

use std::io::{BufRead, Write};
use std::process::{ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::Mutex;
use tauri::Emitter;
use tauri::Manager;
use tauri::PhysicalSize;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

static RECORDING: AtomicBool = AtomicBool::new(false);

/// Authoritative UI state, kept in sync with every emitted state event.
/// The frontend polls `get_state` and reconciles, so a dropped event can
/// never leave the overlay stuck.
static UI_STATE: Mutex<String> = Mutex::new(String::new());

/// Monotonic recording session counter and its start time (epoch ms).
static RECORD_SEQ: AtomicU32 = AtomicU32::new(0);
static RECORD_STARTED_MS: AtomicU64 = AtomicU64::new(0);

const WIN_W: i32 = 190;
const WIN_H: i32 = 56;

static ACTIVE_WINDOW: std::sync::OnceLock<Mutex<Option<String>>> = std::sync::OnceLock::new();

struct Sidecar {
    stdin: Mutex<ChildStdin>,
}

#[derive(Clone, serde::Serialize)]
struct StateEvent {
    state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    seq: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    started_at_ms: Option<u64>,
}

#[derive(serde::Serialize)]
struct StateSnapshot {
    state: String,
    seq: u32,
    started_at_ms: u64,
}

/// Single place that updates the authoritative state, logs it, and emits it.
fn emit_state(
    app: &tauri::AppHandle,
    state: &str,
    mode: Option<&str>,
    seq: Option<u32>,
    started_at_ms: Option<u64>,
) {
    if let Ok(mut guard) = UI_STATE.lock() {
        *guard = state.to_string();
    }
    eprintln!(
        "voiceflow: emit state={state} mode={} seq={} started_at_ms={}",
        mode.unwrap_or("-"),
        seq.map(|v| v.to_string()).unwrap_or_else(|| "-".into()),
        started_at_ms.map(|v| v.to_string()).unwrap_or_else(|| "-".into()),
    );
    let _ = app.emit(
        "voiceflow://state",
        StateEvent {
            state: state.to_string(),
            mode: mode.map(|m| m.to_string()),
            seq,
            started_at_ms,
        },
    );
}

/// Frontend asks for the authoritative state (polled ~1s while visible).
#[tauri::command]
fn get_state() -> StateSnapshot {
    let state = UI_STATE
        .lock()
        .map(|g| g.clone())
        .unwrap_or_else(|_| "hidden".to_string());
    StateSnapshot {
        state,
        seq: RECORD_SEQ.load(Ordering::SeqCst),
        started_at_ms: RECORD_STARTED_MS.load(Ordering::SeqCst),
    }
}

/// Frontend-side tracing into the journal (`voiceflow: ui ...`).
#[tauri::command]
fn ui_log(message: String) {
    eprintln!("voiceflow: ui {message}");
}

fn now_epoch_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn send_command(stdin: &Mutex<ChildStdin>, cmd: &serde_json::Value) {
    if let Ok(mut writer) = stdin.lock() {
        if let Err(e) = writeln!(writer, "{}", cmd) {
            eprintln!("voiceflow: send_command failed: {e}");
        }
    }
}

/// Keep the overlay at the capsule size.
///
/// The configured size is in LOGICAL pixels; on scaled displays (e.g. 200%,
/// Xft.dpi 192) the physical size is multiplied by the scale factor. Setting
/// physical values directly previously halved the window and clipped the
/// capsule, so convert via the window's scale factor instead.
fn enforce_window_size(window: &tauri::WebviewWindow) -> tauri::Result<()> {
    let scale = window.scale_factor().unwrap_or(1.0);
    let want_w = (WIN_W as f64 * scale).round() as u32;
    let want_h = (WIN_H as f64 * scale).round() as u32;
    let current = window.inner_size()?;
    if current.width != want_w || current.height != want_h {
        window.set_size(tauri::LogicalSize::new(WIN_W as f64, WIN_H as f64))?;
    }
    Ok(())
}

fn find_app_root() -> Option<std::path::PathBuf> {
    // 1. Check VOICEFLOW_ROOT env var
    if let Ok(root) = std::env::var("VOICEFLOW_ROOT") {
        let p = std::path::PathBuf::from(&root);
        if p.join("voiceflow_py/daemon.py").exists() {
            log::info!("Using VOICEFLOW_ROOT: {}", p.display());
            return Some(p);
        }
    }
    // 2. System install path
    let sys = std::path::PathBuf::from("/opt/voiceflow");
    if sys.join("voiceflow_py/daemon.py").exists() {
        log::info!("Using system path: {}", sys.display());
        return Some(sys);
    }
    // 3. Current directory
    let cwd = std::env::current_dir().unwrap_or_default();
    if cwd.join("voiceflow_py/daemon.py").exists() {
        return Some(cwd);
    }
    // 4. Parent of current dir (for src-tauri/ build dir)
    if cwd.join("..").join("voiceflow_py/daemon.py").exists() {
        return Some(cwd.join("..").canonicalize().unwrap_or(cwd.join("..")));
    }
    None
}

fn spawn_sidecar(app_root: &std::path::Path) -> Option<(ChildStdin, ChildStdout)> {
    // Try .venv first, then system python3
    let python = app_root.join(".venv/bin/python");
    let python = if python.exists() {
        python
    } else {
        std::path::PathBuf::from("python3")
    };
    let daemon = app_root.join("voiceflow_py/daemon.py");

    if !daemon.exists() {
        eprintln!("voiceflow: sidecar not found: daemon={}", daemon.display());
        log::error!("Sidecar not found: daemon={}", daemon.display());
        return None;
    }

    let mut child = Command::new(&python)
        .arg(&daemon)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .ok()?;

    let stdin = child.stdin.take()?;
    let stdout = child.stdout.take()?;

    std::thread::spawn(move || {
        let _ = child.wait();
    });

    log::info!("Sidecar spawned: python={} daemon={} config=layered",
        python.display(), daemon.display());
    Some((stdin, stdout))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    let state = event.state();
                    if state != ShortcutState::Pressed && state != ShortcutState::Released {
                        return;
                    }

                    let f10 = Shortcut::new(Some(Modifiers::empty()), Code::F10);
                    let f11 = Shortcut::new(Some(Modifiers::empty()), Code::F11);

                    let mode = if *shortcut == f10 {
                        "smart"
                    } else if *shortcut == f11 {
                        "terminal"
                    } else {
                        return;
                    };

                    let sidecar = app.try_state::<Sidecar>();
                    if sidecar.is_none() {
                        return;
                    }
                    let stdin = &sidecar.unwrap().stdin;

                    if state == ShortcutState::Pressed {
                        if RECORDING.load(Ordering::SeqCst) {
                            return;
                        }
                        RECORDING.store(true, Ordering::SeqCst);

                        // New recording session: bump seq and record the start
                        // time so the UI can compute elapsed time authoritatively.
                        let seq = RECORD_SEQ.fetch_add(1, Ordering::SeqCst) + 1;
                        let started_at = now_epoch_ms();
                        RECORD_STARTED_MS.store(started_at, Ordering::SeqCst);

                        // Save the currently active window for later paste targeting
                        let wid = paste::get_active_window();
                        if let Some(lock) = ACTIVE_WINDOW.get() {
                            if let Ok(mut guard) = lock.lock() {
                                *guard = wid;
                            }
                        }

                        send_command(stdin, &serde_json::json!({"type": "start_recording"}));

                        if let Some(w) = app.get_webview_window("main") {
                            // Self-heal: a window created while the session was
                            // still coming up can carry a wrong size (observed
                            // 400x400) and never become visible. Force the
                            // configured size on every press.
                            if let Err(e) = enforce_window_size(&w) {
                                eprintln!("voiceflow: resize failed: {e}");
                            }
                            // Position with the ACTUAL (physical) window size
                            // so the capsule lands centered above the cursor
                            // regardless of display scaling.
                            let sz = w.inner_size().unwrap_or(PhysicalSize::new(
                                WIN_W as u32,
                                WIN_H as u32,
                            ));
                            window_pos::position_near_mouse(
                                &w,
                                sz.width as i32,
                                sz.height as i32,
                            );
                            if let Err(e) = w.show() {
                                eprintln!("voiceflow: window show failed: {e}");
                            }
                            // Re-assert always-on-top on every show: the ABOVE
                            // hint is lost while the window is hidden, so the
                            // capsule would end up behind other windows (e.g. a
                            // maximized editor) and become invisible.
                            if let Err(e) = w.set_always_on_top(true) {
                                eprintln!("voiceflow: set_always_on_top failed: {e}");
                            }

                            // Emit AFTER showing: events sent to a hidden
                            // webview can be dropped, which used to leave the
                            // frontend stuck on a stale "recording" state.
                            emit_state(
                                app,
                                "recording",
                                None,
                                Some(seq),
                                Some(started_at),
                            );

                            // Spawn continuous position tracking thread
                            let window_label = w.label().to_string();
                            let handle = app.app_handle().clone();
                            std::thread::spawn(move || {
                                while RECORDING.load(Ordering::SeqCst) {
                                    std::thread::sleep(std::time::Duration::from_millis(66));
                                    if !RECORDING.load(Ordering::SeqCst) {
                                        break;
                                    }
                                    if let Some(w) = handle.get_webview_window(&window_label) {
                                        if let Ok(sz) = w.inner_size() {
                                            window_pos::position_near_mouse(
                                                &w,
                                                sz.width as i32,
                                                sz.height as i32,
                                            );
                                        }
                                    }
                                }
                            });
                        } else {
                            emit_state(app, "recording", None, Some(seq), Some(started_at));
                        }
                    } else {
                        if !RECORDING.load(Ordering::SeqCst) {
                            return;
                        }
                        RECORDING.store(false, Ordering::SeqCst);

                        send_command(
                            stdin,
                            &serde_json::json!({"type": "stop_and_transcribe", "mode": mode}),
                        );

                        emit_state(app, "transcribing", Some(mode), None, None);
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![get_state, ui_log])
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Authoritative UI state starts hidden
            if let Ok(mut guard) = UI_STATE.lock() {
                *guard = "hidden".to_string();
            }

            // Register hotkeys
            let f10 = Shortcut::new(Some(Modifiers::empty()), Code::F10);
            let f11 = Shortcut::new(Some(Modifiers::empty()), Code::F11);
            app.global_shortcut().register(f10)?;
            app.global_shortcut().register(f11)?;

            // Init active window tracker
            let _ = ACTIVE_WINDOW.set(Mutex::new(None));

            // Resolve app root
            let app_root = match find_app_root() {
                Some(root) => root,
                None => {
                    eprintln!("voiceflow: cannot find root (Python sidecar not found)");
                    log::error!("Cannot find VoiceFlow root (Python sidecar not found)");
                    return Ok(());
                }
            };

            // Spawn Python sidecar
            if let Some((stdin, stdout)) = spawn_sidecar(&app_root) {
                let handle = app.handle().clone();
                let stdin = Mutex::new(stdin);

                std::thread::spawn(move || {
                    let reader = std::io::BufReader::new(stdout);
                    for line in reader.lines() {
                        match line {
                            Ok(text) => {
                                if let Ok(msg) = serde_json::from_str::<serde_json::Value>(&text) {
                                    let msg_type =
                                        msg.get("type").and_then(|v| v.as_str()).unwrap_or("");

                                    match msg_type {
                                        "result" => {
                                            let text = msg
                                                .get("text")
                                                .and_then(|v| v.as_str())
                                                .unwrap_or("");
                                            let shortcut = msg
                                                .get("shortcut")
                                                .and_then(|v| v.as_str())
                                                .unwrap_or("ctrl+v");

                                            let _ = handle.emit("voiceflow://result", msg.clone());

                                            let h = handle.clone();
                                            let text = text.to_string();
                                            let shortcut = shortcut.to_string();
                                            // Get the saved target window
                                            let target_wid = ACTIVE_WINDOW
                                                .get()
                                                .and_then(|lock| lock.lock().ok())
                                                .and_then(|mut guard| guard.take());
                                            std::thread::spawn(move || {
                                                let ok = paste::inject_text(
                                                    &text,
                                                    &shortcut,
                                                    true,
                                                    target_wid.as_deref(),
                                                );
                                                if let Some(sidecar) = h.try_state::<Sidecar>() {
                                                    send_command(
                                                        &sidecar.stdin,
                                                        &serde_json::json!({"type": "paste_complete"}),
                                                    );
                                                }
                                                let state = if ok {
                                                    "inserted"
                                                } else {
                                                    "error"
                                                };
                                                emit_state(&h, state, None, None, None);
                                                let delay = if ok { 400 } else { 800 };
                                                std::thread::sleep(
                                                    std::time::Duration::from_millis(delay),
                                                );
                                                emit_state(&h, "hidden", None, None, None);
                                                // Give the webview a moment to
                                                // process the event before the
                                                // window is hidden.
                                                std::thread::sleep(
                                                    std::time::Duration::from_millis(120),
                                                );
                                                if let Some(w) = h.get_webview_window("main") {
                                                    let _ = w.hide();
                                                }
                                            });
                                        }
                                        "error" => {
                                            log::error!("Sidecar error: {}", text);
                                            eprintln!("voiceflow: sidecar error: {text}");
                                            emit_state(&handle, "error", None, None, None);
                                            let h = handle.clone();
                                            std::thread::spawn(move || {
                                                std::thread::sleep(
                                                    std::time::Duration::from_millis(800),
                                                );
                                                emit_state(&h, "hidden", None, None, None);
                                                std::thread::sleep(
                                                    std::time::Duration::from_millis(120),
                                                );
                                                if let Some(w) = h.get_webview_window("main") {
                                                    let _ = w.hide();
                                                }
                                            });
                                        }
                                        "state" => {
                                            let st = msg
                                                .get("state")
                                                .and_then(|v| v.as_str())
                                                .unwrap_or("");
                                            eprintln!("voiceflow: sidecar state={st}");
                                            if !st.is_empty() && st != "recording" {
                                                // Sidecar hit the max-duration cap
                                                // without a hotkey release: clear the
                                                // recording flag so tracking stops and
                                                // let the UI fall through to transcribing.
                                                if st == "recording_timeout" {
                                                    RECORDING.store(false, Ordering::SeqCst);
                                                    emit_state(
                                                        &handle,
                                                        "transcribing",
                                                        None,
                                                        None,
                                                        None,
                                                    );
                                                } else {
                                                    emit_state(&handle, st, None, None, None);
                                                }
                                            }
                                        }
                                        "level" => {
                                            let _ = handle.emit("voiceflow://level", msg.clone());
                                        }
                                        "ready" => {
                                            log::info!("Python sidecar ready");
                                        }
                                        _ => {
                                            log::debug!("Sidecar message: {}", text);
                                        }
                                    }
                                }
                            }
                            Err(_) => break,
                        }
                    }
                    eprintln!("voiceflow: sidecar stdout reader exited");
                });

                app.manage(Sidecar { stdin });
            } else {
                log::error!("Failed to spawn Python sidecar");
            }

            // Initial window position (hidden)
            if let Some(window) = app.get_webview_window("main") {
                // WebKit2GTK enforces a default minimum viewport size of
                // Lower the WebKitGTK viewport floor, then apply the capsule
                // size (see enforce_window_size for the resizable trick).
                let _ = window.set_min_size(Some(PhysicalSize::new(1, 1)));
                if let Err(e) = enforce_window_size(&window) {
                    eprintln!("voiceflow: initial resize failed: {e}");
                }
                window_pos::position_top_center(&window, WIN_W, WIN_H);
                let _ = window.hide();
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
