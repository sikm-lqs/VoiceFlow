use tauri::PhysicalPosition;

pub fn get_mouse_pos() -> Option<(i32, i32)> {
    let output = std::process::Command::new("xdotool")
        .args(["getmouselocation", "--shell"])
        .output()
        .ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut x: Option<i32> = None;
    let mut y: Option<i32> = None;
    for line in stdout.lines() {
        if let Some((key, val)) = line.split_once('=') {
            match key {
                "X" => x = val.parse().ok(),
                "Y" => y = val.parse().ok(),
                _ => {}
            }
        }
    }
    Some((x?, y?))
}

/// Get full virtual screen size via xrandr (handles multi-monitor).
/// E.g. "Screen 0: minimum 8 x 8, current 8192 x 2304, maximum 32767 x 32767"
fn get_virtual_screen_size() -> Option<(i32, i32)> {
    let output = std::process::Command::new("xrandr")
        .args(["--query"])
        .output()
        .ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        if line.contains("current") {
            // Parse: "Screen 0: minimum 8 x 8, current 8192 x 2304, maximum ..."
            if let Some(idx) = line.find("current") {
                let rest = &line[idx..];
                // Find pattern: "current WWWW x HHHH"
                let parts: Vec<&str> = rest.split_whitespace().collect();
                // parts: ["current", "8192", "x", "2304", ...]
                if parts.len() >= 4 {
                    let w: i32 = parts[1].parse().ok()?;
                    let h_str = parts[3].trim_end_matches(',');
                    let h: i32 = h_str.parse().ok()?;
                    return Some((w, h));
                }
            }
        }
    }
    None
}

pub fn position_near_mouse(window: &tauri::WebviewWindow, win_w: i32, win_h: i32) {
    let (mx, my) = match get_mouse_pos() {
        Some(pos) => pos,
        None => {
            position_top_center(window, win_w, win_h);
            return;
        }
    };

    let (sw, sh) = match get_virtual_screen_size() {
        Some(size) => size,
        None => return,
    };

    // Centered above the cursor so the capsule never covers the
    // caret/target area; flips below when near the top edge.
    let x = (mx - win_w / 2).clamp(8, (sw - win_w - 8).max(8));
    let mut y = my - win_h - 14;
    if y < 8 {
        y = (my + 18).min((sh - win_h - 8).max(8));
    }

    log::debug!(
        "position_near_mouse: mouse=({}, {}) screen={}x{} win=({}, {})",
        mx, my, sw, sh, x, y
    );
    let _ = window.set_position(PhysicalPosition::new(x, y));
}

pub fn position_top_center(window: &tauri::WebviewWindow, win_w: i32, _win_h: i32) {
    let (sw, sh) = match get_virtual_screen_size() {
        Some(size) => size,
        None => return,
    };
    let x = (sw - win_w) / 2;
    let y = (sh as f64 * 0.12) as i32;
    let _ = window.set_position(PhysicalPosition::new(x, y));
}
