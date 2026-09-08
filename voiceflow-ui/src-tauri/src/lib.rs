mod paste;
mod window_pos;

use std::io::{BufRead, Write};
use std::process::{ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use tauri::Emitter;
use tauri::Manager;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

static RECORDING: AtomicBool = AtomicBool::new(false);

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
}

fn send_command(stdin: &Mutex<ChildStdin>, cmd: &serde_json::Value) {
    if let Ok(mut writer) = stdin.lock() {
        if let Err(e) = writeln!(writer, "{}", cmd) {
            eprintln!("voiceflow: send_command failed: {e}");
        }
    }
}

fn find_app_root() -> Option<std::path::PathBuf> {
    // 1. Check VOICEFLOW_ROOT env var
    if let Ok(root) = std::env::var("VOICEFLOW_ROOT") {
        let p = std::path::PathBuf::from(&root);
        if p.join("config.yaml").exists() {
            log::info!("Using VOICEFLOW_ROOT: {}", p.display());
            return Some(p);
        }
    }
    // 2. System install path
    let sys = std::path::PathBuf::from("/opt/voiceflow");
    if sys.join("config.yaml").exists() {
        log::info!("Using system path: {}", sys.display());
        return Some(sys);
    }
    // 3. Current directory
    let cwd = std::env::current_dir().unwrap_or_default();
    if cwd.join("config.yaml").exists() {
        return Some(cwd);
    }
    // 4. Parent of current dir (for src-tauri/ build dir)
    if cwd.join("..").join("config.yaml").exists() {
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

    let config_path = app_root.join("config.yaml");
    let mut child = Command::new(&python)
        .arg(&daemon)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .env("VOICEFLOW_CONFIG", &config_path)
        .spawn()
        .ok()?;

    let stdin = child.stdin.take()?;
    let stdout = child.stdout.take()?;

    std::thread::spawn(move || {
        let _ = child.wait();
    });

    log::info!("Sidecar spawned: python={} daemon={} config={}",
        python.display(), daemon.display(), config_path.display());
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

                        // Save the currently active window for later paste targeting
                        let wid = paste::get_active_window();
                        if let Some(lock) = ACTIVE_WINDOW.get() {
                            if let Ok(mut guard) = lock.lock() {
                                *guard = wid;
                            }
                        }

                        send_command(stdin, &serde_json::json!({"type": "start_recording"}));

                        let _ = app.emit(
                            "voiceflow://state",
                            StateEvent {
                                state: "recording".into(),
                                mode: None,
                            },
                        );

                        if let Some(w) = app.get_webview_window("main") {
                            // Self-heal: a window created while the session was
                            // still coming up can carry a wrong size (observed
                            // 400x400) and never become visible. Force the
                            // configured size on every press.
                            if let Err(e) =
                                w.set_size(tauri::PhysicalSize::new(WIN_W, WIN_H))
                            {
                                eprintln!("voiceflow: set_size failed: {e}");
                            }
                            window_pos::position_near_mouse(&w, WIN_W, WIN_H);
                            if let Err(e) = w.show() {
                                eprintln!("voiceflow: window show failed: {e}");
                            }

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
                                        window_pos::position_near_mouse(&w, WIN_W, WIN_H);
                                    }
                                }
                            });
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

                        let _ = app.emit(
                            "voiceflow://state",
                            StateEvent {
                                state: "transcribing".into(),
                                mode: Some(mode.into()),
                            },
                        );
                    }
                })
                .build(),
        )
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
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
                    eprintln!("voiceflow: cannot find root (config.yaml not found)");
                    log::error!("Cannot find VoiceFlow root (config.yaml not found)");
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
                                                let state = if ok {
                                                    "inserted"
                                                } else {
                                                    "error"
                                                };
                                                let _ = h.emit(
                                                    "voiceflow://state",
                                                    StateEvent {
                                                        state: state.into(),
                                                        mode: None,
                                                    },
                                                );
                                                let delay = if ok { 400 } else { 800 };
                                                std::thread::sleep(
                                                    std::time::Duration::from_millis(delay),
                                                );
                                                let _ = h.emit(
                                                    "voiceflow://state",
                                                    StateEvent {
                                                        state: "hidden".into(),
                                                        mode: None,
                                                    },
                                                );
                                                if let Some(w) = h.get_webview_window("main") {
                                                    let _ = w.hide();
                                                }
                                            });
                                        }
                                        "error" => {
                                            log::error!("Sidecar error: {}", text);
                                            let _ = handle.emit(
                                                "voiceflow://state",
                                                StateEvent {
                                                    state: "error".into(),
                                                    mode: None,
                                                },
                                            );
                                            let h = handle.clone();
                                            std::thread::spawn(move || {
                                                std::thread::sleep(
                                                    std::time::Duration::from_millis(800),
                                                );
                                                let _ = h.emit(
                                                    "voiceflow://state",
                                                    StateEvent {
                                                        state: "hidden".into(),
                                                        mode: None,
                                                    },
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
                                            if !st.is_empty() {
                                                // Sidecar hit the max-duration cap
                                                // without a hotkey release: clear the
                                                // recording flag so tracking stops and
                                                // let the UI fall through to transcribing.
                                                let ui_state = if st == "recording_timeout" {
                                                    RECORDING.store(false, Ordering::SeqCst);
                                                    "transcribing"
                                                } else {
                                                    st
                                                };
                                                let _ = handle.emit(
                                                    "voiceflow://state",
                                                    StateEvent {
                                                        state: ui_state.to_string(),
                                                        mode: None,
                                                    },
                                                );
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
                window_pos::position_top_center(&window, WIN_W, WIN_H);
                let _ = window.hide();
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
