import json
import os
import time
import random
import shutil
from playwright.sync_api import sync_playwright

PATH_TO_VIDEO_FOLDER = "./video"
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720
CURRENT_PAGE = None
CURSOR_X = 640
CURSOR_Y = 360
ZOOM_EVENTS = []
ZOOM_T0 = None

JS_CURSOR_CODE = """
(() => {
    try {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    } catch (e) {}

    function setupCursor() {
        if (document.getElementById('my-cursor-box')) return;
        const parent = document.body || document.documentElement;
        if (!parent) return;

        const style = document.createElement('style');
        style.innerHTML = `
            #my-cursor-box {
                position: fixed;
                top: 0;
                left: 0;
                width: 100vw;
                height: 100vh;
                pointer-events: none;
                z-index: 2147483647;
            }
            #yellow-circle {
                position: absolute;
                width: 64px;
                height: 64px;
                border-radius: 50%;
                background: rgba(255, 235, 59, 0.45);
                transform: translate(-50%, -50%);
                pointer-events: none;
                display: block;
            }
            #white-arrow {
                position: absolute;
                width: 32px;
                height: 32px;
                pointer-events: none;
                display: block;
            }
            .ripple {
                position: absolute;
                border-radius: 50%;
                border: 2px solid rgba(255, 193, 7, 0.9);
                transform: translate(-50%, -50%);
                pointer-events: none;
                animation: ripple-grow 0.5s ease-out forwards;
            }
            @keyframes ripple-grow {
                0% { width: 0px; height: 0px; opacity: 1; }
                100% { width: 64px; height: 64px; opacity: 0; }
            }
        `;
        (document.head || document.documentElement).appendChild(style);

        const box = document.createElement('div');
        box.id = 'my-cursor-box';

        const circle = document.createElement('div');
        circle.id = 'yellow-circle';

        const arrow = document.createElement('div');
        arrow.id = 'white-arrow';
        arrow.innerHTML = `
            <svg width="32" height="32" viewBox="0 0 32 32">
                <path d="M 1 1 L 1 24 L 7 19 L 12 28 L 16 26 L 11 17 L 19 17 Z" 
                      fill="white" stroke="black" stroke-width="2"/>
            </svg>
        `;

        box.appendChild(circle);
        box.appendChild(arrow);
        parent.appendChild(box);
    }

    setupCursor();
    document.addEventListener('DOMContentLoaded', setupCursor);

    window.__setCursorPos = (x, y) => {
        window.__cursorX = x;
        window.__cursorY = y;
        setupCursor();
        const arrow = document.getElementById('white-arrow');
        const circle = document.getElementById('yellow-circle');
        if (arrow && circle) {
            arrow.style.left = x + 'px';
            arrow.style.top = y + 'px';
            circle.style.left = x + 'px';
            circle.style.top = y + 'px';
        }
    };

    window.__showRipple = (x, y) => {
        setupCursor();
        const box = document.getElementById('my-cursor-box');
        if (!box) return;
        const wave = document.createElement('div');
        wave.className = 'ripple';
        wave.style.left = x + 'px';
        wave.style.top = y + 'px';
        box.appendChild(wave);
        setTimeout(() => wave.remove(), 500);
    };
})();
"""

JS_ZOOM_CODE = """
(() => {
    window.__cursorX = window.__cursorX || 0;
    window.__cursorY = window.__cursorY || 0;

    document.addEventListener("mousemove", (event) => {
        window.__cursorX = event.clientX;
        window.__cursorY = event.clientY;
    }, true);

    function showZoomRing(x, y, type) {
        const ring = document.createElement("div");
        ring.style.position = "fixed";
        ring.style.left = x + "px";
        ring.style.top = y + "px";
        ring.style.width = "80px";
        ring.style.height = "80px";
        ring.style.borderRadius = "50%";
        ring.style.border = type === "out"
            ? "4px solid rgba(255, 23, 68, 0.95)"
            : "4px solid rgba(0, 200, 83, 0.95)";
        ring.style.transform = "translate(-50%, -50%)";
        ring.style.pointerEvents = "none";
        ring.style.zIndex = "2147483647";
        ring.style.boxSizing = "border-box";
        (document.body || document.documentElement).appendChild(ring);
        setTimeout(() => ring.remove(), 700);
    }

    window.__zoomAt = (x, y) => {
        const reported = window.pyReportZoom(x, y);
        const draw = (type) => showZoomRing(x, y, type || "in");
        if (reported && typeof reported.then === "function") {
            reported.then(draw);
        } else {
            draw(reported);
        }
    };

    document.addEventListener("keydown", (event) => {
        if (event.key === "F5" || event.keyCode === 116) {
            event.preventDefault();
            event.stopPropagation();
            window.__zoomAt(window.__cursorX || 0, window.__cursorY || 0);
        }
    }, true);
})();
"""


def _record_zoom(x, y):
    zoom_type = "out" if ZOOM_EVENTS and ZOOM_EVENTS[-1]["type"] == "in" else "in"
    ZOOM_EVENTS.append({
        "t": round(time.monotonic() - ZOOM_T0, 3),
        "x": round(float(x), 2),
        "y": round(float(y), 2),
        "type": zoom_type,
    })
    return zoom_type


def write_zoom_sidecar(video_path, events):
    if not events:
        return None
    payload = {
        "viewport": {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        "fps": 25,
        "zoom_factor": 2.0,
        "ramp_sec": 0.5,
        "events": events,
    }
    sidecar = video_path + ".zoom.json"
    with open(sidecar, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)
        handle.write("\n")
    return sidecar


def ShowCallout(text, bgColor="rgba(255, 235, 59, 0.9)", textColor="#000000", fade=True, page=None):
    target_page = page or CURRENT_PAGE
    if not target_page:
        return

    script = """
    ([text, bgColor, textColor, fade]) => {
        const box = document.createElement('div');
        box.innerText = text;
        box.style.position = 'fixed';
        box.style.top = '20px';
        box.style.right = '20px';
        box.style.backgroundColor = bgColor;
        box.style.color = textColor;
        box.style.padding = '10px 20px';
        box.style.fontSize = '16pt';
        box.style.fontWeight = 'bold';
        box.style.borderRadius = '8px';
        box.style.zIndex = '99999999';
        box.style.pointerEvents = 'none';
        box.style.transition = fade ? 'opacity 0.5s ease' : 'none';
        (document.body || document.documentElement).appendChild(box);
        
        setTimeout(() => {
            box.style.opacity = '0';
            setTimeout(() => box.remove(), 500);
        }, 2000);
    }
    """
    try:
        target_page.evaluate(script, [text, bgColor, textColor, fade])
    except Exception:
        pass


def smooth_move(page, target_x, target_y, steps=25):
    global CURSOR_X, CURSOR_Y
    start_x, start_y = CURSOR_X, CURSOR_Y

    for i in range(1, steps + 1):
        curr_x = start_x + (target_x - start_x) * (i / steps)
        curr_y = start_y + (target_y - start_y) * (i / steps)
        try:
            page.evaluate("([x, y]) => window.__setCursorPos(x, y)", [curr_x, curr_y])
        except Exception:
            pass
        time.sleep(0.015)

    page.mouse.move(target_x, target_y)
    CURSOR_X, CURSOR_Y = target_x, target_y
    time.sleep(0.15)


def click_at(page, x, y):
    smooth_move(page, x, y)
    try:
        page.evaluate("([x, y]) => window.__showRipple(x, y)", [x, y])
    except Exception:
        pass
    page.mouse.click(x, y)
    time.sleep(0.2)


def human_type(page, text):
    for char in text:
        page.keyboard.type(char)
        time.sleep(random.uniform(0.04, 0.10))
    time.sleep(0.3)


def CaptureVideo_Magento(
    url,
    company_name,
    language,
    country,
    postcode,
    city,
    street,
    house_no,
):
    global CURRENT_PAGE, CURSOR_X, CURSOR_Y, ZOOM_T0
    os.makedirs(PATH_TO_VIDEO_FOLDER, exist_ok=True)

    CURSOR_X = 640
    CURSOR_Y = 360
    ZOOM_EVENTS.clear()
    ZOOM_T0 = time.monotonic()

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"]
            )

            context = browser.new_context(
                record_video_dir=PATH_TO_VIDEO_FOLDER,
                record_video_size={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
                viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}
            )

            context.add_init_script(JS_CURSOR_CODE)
            context.add_init_script(JS_ZOOM_CODE)
            page = context.new_page()
            page.expose_function("pyReportZoom", _record_zoom)
            CURRENT_PAGE = page

            page.goto(url, wait_until="domcontentloaded")
            time.sleep(1)

            page.evaluate("([x, y]) => window.__setCursorPos(x, y)", [CURSOR_X, CURSOR_Y])
            time.sleep(0.3)

            ShowCallout(f"Company: {company_name} ({city})")

            search_input = page.locator('textarea[name="q"]').first
            search_input.wait_for(state="visible", timeout=15000)
            box = search_input.bounding_box()
            if box:
                click_at(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

            search_query = f"Magento developer in {city}"
            human_type(page, search_query)

            search_button = page.locator('button[type="submit"]:visible, form button[type="submit"]:visible').first
            try:
                search_button.wait_for(state="visible", timeout=4000)
                btn_box = search_button.bounding_box()
                if btn_box:
                    click_at(page, btn_box["x"] + btn_box["width"] / 2, btn_box["y"] + btn_box["height"] / 2)
                else:
                    search_button.click()
            except Exception:
                page.keyboard.press("Enter")

            first_result = page.locator('a[data-testid="result-title-a"]').first
            first_result.wait_for(state="visible", timeout=25000)
            time.sleep(1)

            link_box = first_result.bounding_box()
            if link_box:
                click_at(page, link_box["x"] + link_box["width"] / 2, link_box["y"] + link_box["height"] / 2)
            else:
                first_result.click()

            time.sleep(2.5)

            video = page.video
            context.close()
            browser.close()

            temp_path = video.path()
            final_path = os.path.join(PATH_TO_VIDEO_FOLDER, f"{company_name}.webm")
            final_path = os.path.abspath(final_path)

            if os.path.exists(temp_path):
                shutil.move(temp_path, final_path)
                write_zoom_sidecar(final_path, list(ZOOM_EVENTS))
                return final_path
            else:
                return None

        except Exception:
            return None
