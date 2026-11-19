use std::io::Write;
use std::process::Command;

/// Pre-keystroke timing budget (ms). FOCUS_SETTLE covers the X focus
/// change; CLIPBOARD_SETTLE covers X selection propagation between the
/// clipboard write and the paste keystroke. These sit on the perceived
/// latency path, so they are kept as tight as safely possible.
const FOCUS_SETTLE_MS: u64 = 30;
const CLIPBOARD_SETTLE_MS: u64 = 40;
/// Post-keystroke delays do not affect perceived latency (the text is
/// already pasted); they only space out logging and the clipboard
/// restore, protecting apps that read the selection slightly late.
/// Keep them generous — trimming them buys nothing the user can feel.
const PASTE_SETTLE_MS: u64 = 100;
const RESTORE_CLIPBOARD_DELAY_MS: u64 = 500;

/// Save the currently active window ID via xdotool.
pub fn get_active_window() -> Option<String> {
    let output = Command::new("xdotool")
        .args(["getactivewindow"])
        .output()
        .ok()?;
    if output.status.success() {
        Some(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        None
    }
}

/// Focus a specific window before pasting.
fn focus_window(window_id: &str) -> bool {
    Command::new("xdotool")
        .args(["windowfocus", "--sync", window_id])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

pub fn inject_text(
    text: &str,
    shortcut: &str,
    restore_clipboard: bool,
    target_window: Option<&str>,
) -> bool {
    if text.is_empty() {
        log::warn!("inject_text: empty text");
        return false;
    }

    log::info!(
        "inject_text: text_len={}, shortcut={}, target_window={:?}",
        text.len(),
        shortcut,
        target_window
    );

    // Snapshot the clipboard concurrently with focus restoration; the
    // handle is joined below, strictly before the clipboard is overwritten.
    let saved_handle = if restore_clipboard {
        Some(std::thread::spawn(save_clipboard))
    } else {
        None
    };

    // Restore focus to the original window before pasting
    if let Some(wid) = target_window {
        if !focus_window(wid) {
            log::warn!("inject_text: failed to focus window {}", wid);
        }
        // Small delay for focus to take effect
        std::thread::sleep(std::time::Duration::from_millis(FOCUS_SETTLE_MS));
    }

    let saved = saved_handle.and_then(|handle| handle.join().unwrap_or(None));

    if !set_clipboard(text) {
        log::error!("inject_text: failed to set clipboard");
        return false;
    }
    std::thread::sleep(std::time::Duration::from_millis(CLIPBOARD_SETTLE_MS));

    if !send_paste(shortcut) {
        log::error!("inject_text: failed to send paste");
        return false;
    }
    std::thread::sleep(std::time::Duration::from_millis(PASTE_SETTLE_MS));

    log::info!("inject_text: paste sent successfully");

    if let Some(data) = saved {
        std::thread::sleep(std::time::Duration::from_millis(
            RESTORE_CLIPBOARD_DELAY_MS,
        ));
        restore_clipboard_data(&data);
    }

    true
}

fn save_clipboard() -> Option<Vec<u8>> {
    let output = Command::new("xclip")
        .args(["-sel", "clip", "-o"])
        .output()
        .ok()?;
    if output.status.success() {
        Some(output.stdout)
    } else {
        None
    }
}

fn set_clipboard(text: &str) -> bool {
    Command::new("xclip")
        .args(["-sel", "clip"])
        .stdin(std::process::Stdio::piped())
        .spawn()
        .and_then(|mut child| {
            if let Some(stdin) = child.stdin.as_mut() {
                stdin.write_all(text.as_bytes())?;
            }
            child.wait()
        })
        .map(|s| s.success())
        .unwrap_or(false)
}

fn send_paste(shortcut: &str) -> bool {
    Command::new("xdotool")
        .args(["key", "--clearmodifiers", shortcut])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn restore_clipboard_data(data: &[u8]) {
    let _ = Command::new("xclip")
        .args(["-sel", "clip"])
        .stdin(std::process::Stdio::piped())
        .spawn()
        .and_then(|mut child| {
            if let Some(stdin) = child.stdin.as_mut() {
                stdin.write_all(data)?;
            }
            child.wait()
        });
}
