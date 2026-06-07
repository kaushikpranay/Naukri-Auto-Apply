import sys
import json
import tkinter as tk
from tkinter import font as tkfont
import ctypes

def main():
    # Enable DPI awareness on Windows for crisp fonts and layout scaling
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetThreadDpiAwarenessContext(-1)
        except Exception:
            pass

    # Read input parameters from stdin
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}

    question_text = input_data.get("question_text", "")
    options = input_data.get("options", [])
    stored_answer = input_data.get("stored_answer")
    is_case2 = input_data.get("is_case2", False)

    result = {"answer": None, "selected_option": None}

    # Initialize Tkinter Window
    root = tk.Tk()
    root.title("Naukri Automation — Human in the Loop")
    root.geometry("620x480")
    root.configure(bg="#0f0f16")
    root.attributes("-topmost", True)
    root.resizable(True, True)
    
    # Center the window on the screen
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")
    
    root.lift()
    root.focus_force()

    # Premium dark theme styling
    bg_color = "#0f0f16"
    fg_color = "#ffffff"
    accent_color = "#0284c7"
    accent_hover = "#0369a1"
    secondary_fg = "#9ca3af"
    muted_fg = "#6b7280"
    card_bg = "#1e1b4b"  # Deep indigo/dark violet background for context card
    input_bg = "#1e293b"
    btn_skip_bg = "#3b0a1a"
    btn_skip_fg = "#f43f5e"
    btn_skip_hover = "#4c0519"

    # Layout padding helper
    padding = {"padx": 24, "pady": 10}

    # 1. Section Header: Question category
    header_label = tk.Label(
        root, 
        text="ACTION REQUIRED: UNKNOWN QUESTION", 
        bg=bg_color, 
        fg=accent_color,
        font=("Segoe UI", 9, "bold")
    )
    header_label.pack(anchor="w", padx=24, pady=(20, 2))

    # 2. Main Question Label
    question_label = tk.Label(
        root, 
        text=question_text, 
        bg=bg_color, 
        fg=fg_color,
        font=("Segoe UI", 13, "bold"), 
        wraplength=570, 
        justify="left"
    )
    question_label.pack(anchor="w", padx=24, pady=(0, 10))

    # 3. Contextual Box (Case 2: Mapping indicator)
    if is_case2 and stored_answer:
        ctx_frame = tk.Frame(root, bg=card_bg, padx=12, pady=8, bd=0)
        ctx_frame.pack(fill="x", padx=24, pady=(0, 10))
        
        info_icon = "ℹ "
        tk.Label(
            ctx_frame, 
            text=f"{info_icon}Existing answer is '{stored_answer}'. Please map it to one of the options below:", 
            bg=card_bg, 
            fg="#c084fc",
            font=("Segoe UI", 9, "italic"),
            wraplength=540,
            justify="left"
        )
        ctx_frame.pack()
        # Ensure label inside context fits and wraps correctly
        for child in ctx_frame.winfo_children():
            child.pack(anchor="w")

    # Divider line
    divider = tk.Frame(root, bg="#27273a", height=1)
    divider.pack(fill="x", padx=24, pady=5)

    # 4. Interactive Body Area
    body_frame = tk.Frame(root, bg=bg_color)
    body_frame.pack(fill="both", expand=True, padx=24, pady=10)

    selected_var = tk.StringVar(value="")

    if options:
        # Multiple Choice selection (Radio buttons)
        tk.Label(
            body_frame, 
            text="Select the most appropriate option:", 
            bg=bg_color, 
            fg=secondary_fg,
            font=("Segoe UI", 10, "bold")
        )
        body_frame.winfo_children()[-1].pack(anchor="w", pady=(0, 8))

        # Scrollable option panel for high number of options
        canvas = tk.Canvas(body_frame, bg=bg_color, highlightthickness=0)
        scrollbar = tk.Scrollbar(body_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=bg_color)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Build radio button choices
        for opt in options:
            rb_frame = tk.Frame(scrollable_frame, bg=bg_color, pady=2)
            rb_frame.pack(fill="x", anchor="w")
            
            rb = tk.Radiobutton(
                rb_frame, 
                text=opt, 
                variable=selected_var, 
                value=opt,
                bg=bg_color, 
                fg="#e5e7eb", 
                selectcolor="#1e293b",
                font=("Segoe UI", 11), 
                activebackground=bg_color,
                activeforeground=fg_color,
                highlightthickness=0
            )
            rb.pack(anchor="w")

        canvas.pack(side="left", fill="both", expand=True)
        
        # Only show scrollbar if options exceed height
        if len(options) > 6:
            scrollbar.pack(side="right", fill="y")
        
        # Enable MouseWheel scrolling for the canvas
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def on_submit():
            val = selected_var.get() or None
            # If is_case2 is True and we're mapping options, we return the stored answer as the 'answer'
            # and the chosen option as the 'selected_option'. Otherwise, both are the same.
            result["answer"] = stored_answer if (is_case2 and stored_answer is not None) else val
            result["selected_option"] = val
            root.destroy()

    else:
        # Free-text input field
        tk.Label(
            body_frame, 
            text="Type your answer:", 
            bg=bg_color, 
            fg=secondary_fg,
            font=("Segoe UI", 10, "bold")
        )
        body_frame.winfo_children()[-1].pack(anchor="w", pady=(0, 8))

        entry_frame = tk.Frame(body_frame, bg="#1e293b", bd=1, relief="flat")
        entry_frame.pack(fill="x", pady=4)

        entry = tk.Entry(
            entry_frame, 
            bg=input_bg, 
            fg=fg_color, 
            insertbackground=fg_color,
            font=("Segoe UI", 12), 
            relief="flat", 
            bd=8
        )
        entry.pack(fill="x")
        entry.focus_set()
        
        def on_submit():
            val = entry.get().strip() or None
            result["answer"] = val
            result["selected_option"] = val
            root.destroy()

        entry.bind("<Return>", lambda e: on_submit())

    # Skip/Cancel handler
    def on_skip():
        result["answer"] = None
        result["selected_option"] = None
        root.destroy()

    # Handle window close (X button) as skip/cancel
    root.protocol("WM_DELETE_WINDOW", on_skip)

    # 5. Bottom Navigation / Buttons Bar
    btn_frame = tk.Frame(root, bg=bg_color)
    btn_frame.pack(fill="x", padx=24, pady=(15, 25))

    # Hover animations helpers
    def on_enter(e, widget, bg):
        widget.configure(bg=bg)
    def on_leave(e, widget, bg):
        widget.configure(bg=bg)

    skip_btn = tk.Button(
        btn_frame, 
        text="Skip / Cancel", 
        command=on_skip,
        bg=btn_skip_bg, 
        fg=btn_skip_fg, 
        font=("Segoe UI", 10, "bold"),
        relief="flat", 
        padx=18, 
        pady=8, 
        cursor="hand2"
    )
    skip_btn.pack(side="left")
    skip_btn.bind("<Enter>", lambda e: on_enter(e, skip_btn, btn_skip_hover))
    skip_btn.bind("<Leave>", lambda e: on_leave(e, skip_btn, btn_skip_bg))

    submit_btn = tk.Button(
        btn_frame, 
        text="Submit Answer", 
        command=on_submit,
        bg=accent_color, 
        fg=fg_color, 
        font=("Segoe UI", 10, "bold"),
        relief="flat", 
        padx=20, 
        pady=8, 
        cursor="hand2"
    )
    submit_btn.pack(side="right")
    submit_btn.bind("<Enter>", lambda e: on_enter(e, submit_btn, accent_hover))
    submit_btn.bind("<Leave>", lambda e: on_leave(e, submit_btn, accent_color))

    # Start main Tkinter loop
    root.mainloop()

    # Output JSON string back to stdout
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()

if __name__ == "__main__":
    main()
