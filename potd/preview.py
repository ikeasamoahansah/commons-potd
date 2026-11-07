"""Tkinter preview window for choosing between the day's images."""

from __future__ import annotations

import base64
import io
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from .api import PotdImage


@dataclass
class PreviewItem:
    """One candidate image to show in the chooser."""

    title: str          # file name, e.g. "Something.jpg"
    detail: str         # e.g. "6000 × 4000 — double-click to use"
    path: Path          # local preview file (JPEG/PNG/GIF/WebP …)
    source_url: str     # Commons description page (right-click to open)


_SELECT_COLOR = "#3584e4"   # GNOME accent blue
_BG_COLOR = "#1e1e1e"
_CARD_COLOR = "#2a2a2a"

try:
    from PIL import Image  # Pillow: converts JPEG etc. for Tkinter
    _HAVE_PIL = True
except ImportError:  # pragma: no cover - depends on the environment
    _HAVE_PIL = False


def render_photo(path: Path, box_w: int, box_h: int) -> tk.PhotoImage:
    """Load ``path`` resized to fit in ``(box_w, box_h)`` as a PhotoImage.

    Tk's PhotoImage cannot read JPEG, so with Pillow available the file is
    converted to PNG in memory; without it only PNG/GIF files can be shown.
    """
    if _HAVE_PIL:
        with Image.open(path) as img:
            img.thumbnail((box_w, box_h), Image.LANCZOS)
            if img.mode in ("RGBA", "P", "CMYK") or img.format != "PNG":
                img = img.convert("RGB")
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
        return tk.PhotoImage(data=base64.b64encode(buffer.getvalue()))
    photo = tk.PhotoImage(file=str(path))
    factor = max(1, -(-photo.width() // box_w), -(-photo.height() // box_h))
    return photo.subsample(factor) if factor > 1 else photo


def show_chooser(items: list[PreviewItem], date_text: str) -> int | None:
    """Show a window with one card per candidate.

    Click a card to select it; double-click or press Return to apply.
    Right-click a card to open its Commons file page in a browser.
    Returns the selected index, or ``None`` if the user cancelled.
    """
    root = tk.Tk(className="potd")
    root.title(f"Picture of the Day — {date_text}")
    root.configure(bg=_BG_COLOR)

    screen_w = max(root.winfo_screenwidth(), 800)
    screen_h = max(root.winfo_screenheight(), 600)

    n = len(items)
    # Card image box: fit every card side by side with margins.
    box_w = max(280, min(640, (screen_w - 96) // max(n, 1)))
    box_h = max(220, min(720, screen_h - 320))

    selected: list[int] = [-1]
    chosen: list[int | None] = [None]

    def paint_cards() -> None:
        for i, frame in enumerate(frames):
            on = i == selected[0]
            color = _SELECT_COLOR if on else "#444444"
            frame.configure(highlightthickness=3, highlightbackground=color)

    def select(i: int) -> None:
        selected[0] = i
        paint_cards()

    def apply() -> None:
        if selected[0] >= 0:
            chosen[0] = selected[0]
            root.destroy()

    def open_page(i: int) -> None:
        if items[i].source_url:
            webbrowser.open(items[i].source_url)

    frames: list[tk.Frame] = []
    load_error: str | None = None
    for i, item in enumerate(items):
        frame = tk.Frame(root, bg=_CARD_COLOR, padx=8, pady=8,
                         highlightthickness=3, highlightbackground="#444444",
                         cursor="hand2")
        frame.grid(row=0, column=i, padx=8, pady=(16, 4), sticky="n")
        frames.append(frame)

        try:
            photo = render_photo(item.path, box_w, box_h)
        except Exception as exc:  # unreadable format / missing Pillow
            photo = None
            load_error: str | None = str(exc)
        image_label = tk.Label(frame, image=photo, bg=_CARD_COLOR)
        image_label.image = photo  # keep a reference (PhotoImage GC pitfall)
        image_label.pack()
        if photo is None and load_error and not _HAVE_PIL:
            tk.Label(frame, text="Install Pillow to preview this format:\n"
                                 "pip install pillow",
                     bg=_CARD_COLOR, fg="#ff8f8f",
                     font=("sans-serif", 9, "italic"), wraplength=box_w,
                     justify="center").pack(fill="x", pady=6)

        tk.Label(frame, text=item.title, bg=_CARD_COLOR, fg="white",
                 font=("sans-serif", 10, "bold"), wraplength=box_w,
                 justify="center").pack(fill="x", pady=(6, 0))
        tk.Label(frame, text=item.detail, bg=_CARD_COLOR, fg="#aaaaaa",
                 font=("sans-serif", 9)).pack()

        for widget in (frame, image_label):
            widget.bind("<Button-1>", lambda _e, i=i: select(i))
            widget.bind("<Double-Button-1>", lambda _e, i=i: (
                select(i), apply()))
            widget.bind("<Button-3>", lambda _e, i=i: (
                select(i), open_page(i)))

    root.bind("<Return>", lambda _e: apply())
    root.bind("<Escape>", lambda _e: root.destroy())

    button_row = tk.Frame(root, bg=_BG_COLOR)
    button_row.grid(row=1, column=0, columnspan=max(n, 1),
                    pady=(8, 4), sticky="ew")
    tk.Button(button_row, text="Set wallpaper", command=apply,
              padx=12).pack(side="left", padx=8)
    tk.Button(button_row, text="Quit (Esc)", command=root.destroy,
              padx=12).pack(side="right", padx=8)

    tk.Label(
        root,
        text="click to select · double-click / Return to set wallpaper · "
             "right-click opens the Commons page · Esc quits",
        bg=_BG_COLOR, fg="#777777", font=("sans-serif", 8),
    ).grid(row=2, column=0, columnspan=max(n, 1), pady=(0, 10))

    paint_cards()
    root.update_idletasks()
    root.geometry(f"+{(root.winfo_screenwidth() - root.winfo_reqwidth()) // 2}"
                  f"+{(root.winfo_screenheight() - root.winfo_reqheight()) // 2}")
    root.lift()
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.mainloop()
    return chosen[0]
