import importlib
import sys
import types
import unittest


def _install_tk_stub() -> None:
    tk = types.ModuleType("tkinter")
    tk.Tk = type("Tk", (), {})
    tk.Frame = type("Frame", (), {})
    tk.LabelFrame = type("LabelFrame", (), {})
    tk.StringVar = type("StringVar", (), {})
    tk.BooleanVar = type("BooleanVar", (), {})
    tk.IntVar = type("IntVar", (), {})
    tk.Entry = type("Entry", (), {})
    tk.Button = type("Button", (), {})
    tk.Checkbutton = type("Checkbutton", (), {})
    tk.Text = type("Text", (), {})
    tk.END = "end"
    tk.BOTH = "both"
    tk.WORD = "word"
    tk.LEFT = "left"
    tk.RIGHT = "right"
    tk.X = "x"
    tk.Y = "y"

    ttk = types.ModuleType("tkinter.ttk")
    ttk.Notebook = type("Notebook", (), {})
    ttk.Frame = type("Frame", (), {})
    ttk.Treeview = type("Treeview", (), {})
    ttk.Scrollbar = type("Scrollbar", (), {})

    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.showwarning = lambda *args, **kwargs: None
    messagebox.showinfo = lambda *args, **kwargs: None
    messagebox.showerror = lambda *args, **kwargs: None

    scrolledtext = types.ModuleType("tkinter.scrolledtext")
    scrolledtext.ScrolledText = type("ScrolledText", (), {})

    sys.modules.setdefault("tkinter", tk)
    sys.modules.setdefault("tkinter.ttk", ttk)
    sys.modules.setdefault("tkinter.messagebox", messagebox)
    sys.modules.setdefault("tkinter.scrolledtext", scrolledtext)


class UiLocalModelPanelTests(unittest.TestCase):
    def test_ui_app_import_headless(self) -> None:
        _install_tk_stub()
        module = importlib.import_module("tools.ui_app")
        self.assertTrue(hasattr(module, "LOCAL_MODEL_MARKERS"))

    def test_local_model_ui_markers_gate(self) -> None:
        from tools import verify_consistency

        results = verify_consistency.check_local_model_ui_markers()
        self.assertTrue(results)
        self.assertEqual(results[0].status, "OK")


if __name__ == "__main__":
    unittest.main()
