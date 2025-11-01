import os, sys, os.path

# Попробуем автоматически вычислить путь к tcl/tk в вашей установке Python
base = getattr(sys, "base_prefix", sys.prefix)  # для venv вернёт путь к базовому Python
tcl_dir = os.path.join(base, "tcl")

# Если знаете точный путь — можно прописать его жестко:
# tcl_dir = r"C:\Users\а\AppData\Local\Programs\Python\Python313\tcl"

os.environ.setdefault("TCL_LIBRARY", os.path.join(tcl_dir, "tcl8.6"))
os.environ.setdefault("TK_LIBRARY",  os.path.join(tcl_dir, "tk8.6"))

import tkinter as tk
memory = []
settings = {
    "package": "default",
    "URL": "None",
    "mode": "false",
    "version": "1.0.0",
    "filter": "None"
}

root = tk.Tk()
root.geometry("600x600")

f = tk.Frame(root)
f.pack(fill="both", expand=True)

sb = tk.Scrollbar(f)
sb.pack(side="right", fill="y")

t = tk.Text(f, wrap="word", yscrollcommand=sb.set, state="disabled")
t.config(state="normal")
for k, v in settings.items():
    t.insert("end", f"{k}: {v}\n")
t.config(state="disabled")
t.see("end")

t.pack(side="left", fill="both", expand=True)
sb.config(command=t.yview)

e = tk.Entry(root)
e.pack(fill="x", padx=10, pady=(6, 12))

def add(_=None):
    s = e.get().strip()
    if s:
        t.config(state="normal")
        t.insert("end", s + "\n")
        t.config(state="disabled")
        t.see("end")
        e.delete(0, "end")
    memory.append(s)

e.bind("<Return>", add)

root.mainloop()

