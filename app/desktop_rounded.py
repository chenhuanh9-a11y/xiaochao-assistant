"""Small ttk skins; native labels, entry buffers, states and bindings are retained.

Only the stretchable border is drawn. No content is rasterized, no global theme
or scaling is changed, and there are no event bindings/timers/window operations.
Classic appearance aliases keep the existing view and WindowDisplay protocol.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, font as tkfont

from PIL import Image, ImageDraw, ImageTk

from desktop_theme import (BG, SURFACE, INPUT_BG, LINE, TEXT, MUTED, PRIMARY,
                           BUTTON_BG, BUTTON_HOVER, BUTTON_TEXT, FONT,
                           CARD_RADIUS_DIP, CONTROL_RADIUS_DIP)


def _parent_background(parent):
    try:
        return str(parent.cget("bg"))
    except tk.TclError:
        style = ttk.Style(parent)
        return style.lookup(str(parent.cget("style")) or parent.winfo_class(), "background") or BG


class _Skins:
    def __init__(self, root):
        self.root = root
        self.style = ttk.Style(root)
        self.cache = {}
        self.images = []  # ttk elements keep Tcl names, so retain their owners.

    def font(self, value):
        font = value if isinstance(value, tkfont.Font) else tkfont.Font(root=self.root, font=value)
        actual = font.actual()
        return (actual["family"], int(font.cget("size")), actual["weight"], actual["slant"])

    def color(self, value):
        return "#%02x%02x%02x" % tuple(round(v/257) for v in self.root.winfo_rgb(value))

    def picture(self, fill, outline, parent, radius, width, *, focus=False, button=False):
        radius = max(1, round(radius))
        # ttk tiles the center of an image element. A tiny center causes
        # thousands of GDI copies for large cards; keep a broad solid center.
        extent = radius*2+128
        factor = 4
        image = Image.new("RGB", (extent*factor, extent*factor), self.color(parent))
        draw = ImageDraw.Draw(image)
        bounds = (1, 1, extent*factor-2, extent*factor-2)
        draw.rounded_rectangle(bounds, radius=radius*factor, fill=self.color(fill),
                               outline=self.color(outline), width=max(0, round(width*factor)))
        if focus and button:
            inset = max(3, round(radius*.4))*factor
            draw.rounded_rectangle((inset, inset, extent*factor-inset-1, extent*factor-inset-1),
                radius=max(1, radius*factor-inset), fill=self.color(fill),
                outline=self.color(parent), width=factor)
        photo = ImageTk.PhotoImage(image.resize((extent, extent), Image.Resampling.LANCZOS), master=self.root)
        self.images.append(photo)
        return photo

    def get(self, kind, props, parent, scale):
        props = dict(props)
        if kind == "button":
            props["font"] = self.font(props["font"])
        key = self.style.theme_use(), kind, tuple(sorted(props.items())), parent, scale
        if key in self.cache:
            return self.cache[key]
        suffix = {"button": "TButton", "frame": "TFrame", "entry": "TEntry", "combo": "TCombobox"}[kind]
        name = f"XiaochaoRound{len(self.cache)+1}.{suffix}"
        element = name + ".surface"
        radius = max(1, round(props["radius"]*scale))
        edge = props["highlightbackground"]
        width = props["highlightthickness"]
        normal = self.picture(props["bg"], edge, parent, radius, width)
        image_spec = [normal]
        if kind != "frame":
            disabled = self.picture(INPUT_BG, LINE, parent, radius, width)
            focus = self.picture(props["bg"], props["highlightcolor"], parent, radius,
                                 max(2, round(2*scale)), focus=True, button=kind == "button")
            image_spec += [("disabled", disabled)]
            if kind == "button":
                pressed = self.picture(props["activebackground"], props["highlightcolor"], parent, radius, width)
                hover_focus = self.picture(props["activebackground"], props["highlightcolor"], parent, radius,
                    max(2, round(2*scale)), focus=True, button=True)
                hover = self.picture(props["activebackground"], edge, parent, radius, width)
                image_spec += [("pressed", pressed), ("focus", "active", hover_focus)]
            image_spec += [("focus", focus)]
            if kind == "button":
                image_spec += [("active", hover)]
        self.style.element_create(element, "image", *image_spec,
            border=radius+1, padding=0, width=0, height=0, sticky="nsew")
        if kind == "frame":
            layout = [(element, {"sticky": "nsew"})]
        elif kind == "button":
            layout = [(element, {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})]
        elif kind == "entry":
            layout = [(element, {"sticky": "nsew", "children": [
                ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})]
        else:
            layout = [(element, {"sticky": "nsew", "children": [
                ("Combobox.padding", {"sticky": "nsew", "children": [
                    ("Combobox.downarrow", {"side": "right", "sticky": ""}),
                    ("Combobox.textarea", {"sticky": "nsew"})]})]})]
        self.style.layout(name, layout)
        options = dict(background=parent)
        if kind == "button":
            options.update(font=props["font"], padding=(props["padx"], props["pady"]),
                           foreground=props["fg"], anchor=props["anchor"], width=0)
            self.style.map(name, foreground=[("disabled", props["disabledforeground"]),
                                            ("active", props["activeforeground"])])
        elif kind in {"entry", "combo"}:
            options.update(padding=(props["borderwidth"], props["borderwidth"]), foreground=props["fg"],
                fieldbackground=props["bg"], insertcolor=props["insertbackground"],
                selectbackground=PRIMARY, selectforeground=SURFACE, arrowcolor=PRIMARY,
                background=props["bg"], bordercolor=props["bg"], lightcolor=props["bg"], darkcolor=props["bg"])
            self.style.map(name, foreground=[("disabled", MUTED)])
        self.style.configure(name, **options)
        self.cache[key] = name
        return name


class _Appearance:
    def _prepare(self, master, kind, kwargs):
        self._round_ready = False
        self._kind = kind
        self._props = dict(bg=SURFACE, highlightbackground=LINE, highlightcolor=PRIMARY,
                           highlightthickness=1, radius=CARD_RADIUS_DIP if kind == "frame" else CONTROL_RADIUS_DIP)
        self._aliases = dict(bg="bg", background="bg", highlightbackground="highlightbackground",
            highlightcolor="highlightcolor", highlightthickness="highlightthickness", radius="radius")
        if kind == "button":
            self._props.update(bg=BUTTON_BG, fg=BUTTON_TEXT, activebackground=BUTTON_HOVER,
                activeforeground=BUTTON_TEXT, disabledforeground=MUTED, font=(FONT, 10, "bold"),
                padx=15, pady=9, anchor="center", borderwidth=0, relief="flat")
            self._aliases.update({key: key for key in ("activebackground", "activeforeground", "disabledforeground", "font", "padx", "pady", "anchor", "borderwidth", "relief")})
            self._aliases.update(fg="fg", foreground="fg", bd="borderwidth")
        elif kind == "frame":
            self._props.update(padx=0, pady=0, borderwidth=0)
            self._aliases.update(padx="padx", pady="pady", borderwidth="borderwidth", bd="borderwidth")
        else:
            self._props.update(bg=INPUT_BG, fg=TEXT, borderwidth=7, relief="flat", insertbackground=TEXT)
            self._aliases.update(fg="fg", foreground="fg", bd="borderwidth", borderwidth="borderwidth",
                                 relief="relief", insertbackground="insertbackground")
        native = {}
        for name, value in kwargs.items():
            if name in self._aliases:
                self._props[self._aliases[name]] = value
            else:
                native[name] = value
        root = master._root()
        if not hasattr(root, "_xiaochao_round_skins"):
            root._xiaochao_round_skins = _Skins(root)
        self._skins = root._xiaochao_round_skins
        return native

    def _finish(self):
        self._round_ready = True
        self._restyle()

    def _restyle(self):
        scale = float(getattr(self.winfo_toplevel(), "_xiaochao_round_scale", 1.0))
        style = self._skins.get(self._kind, self._props, _parent_background(self.master), scale)
        super().configure(style=style)
        if self._kind == "frame":
            super().configure(padding=(self._props["padx"], self._props["pady"]))

    def configure(self, cnf=None, **kwargs):
        if not getattr(self, "_round_ready", False):
            return super().configure(cnf, **kwargs)
        if isinstance(cnf, str):
            if cnf in self._aliases:
                return (cnf, cnf, cnf, "", self.cget(cnf))
            return super().configure(cnf)
        if cnf:
            kwargs = dict(cnf, **kwargs)
        if not kwargs:
            result = super().configure()
            result.update({key: (key, key, key, "", self.cget(key)) for key in self._aliases})
            return result
        native, changed = {}, False
        for name, value in kwargs.items():
            if name in self._aliases:
                prop = self._aliases[name]
                if prop == "font":
                    value = self._skins.font(value)
                if self._props[prop] != value:
                    self._props[prop] = value
                    changed = True
            else:
                native[name] = value
        result = super().configure(**native) if native else None
        if changed:
            self._restyle()
        return result

    config = configure

    def cget(self, name):
        if name in self._aliases:
            return self._props[self._aliases[name]]
        return super().cget(name)

    __getitem__ = cget

    def keys(self):
        return list(dict.fromkeys([*super().keys(), *self._aliases]))


class RoundedButton(_Appearance, ttk.Button):
    def __init__(self, master, **kwargs):
        options = self._prepare(master, "button", kwargs)
        ttk.Button.__init__(self, master, **options)
        self._finish()


class RoundedFrame(_Appearance, ttk.Frame):
    def __init__(self, master, **kwargs):
        options = self._prepare(master, "frame", kwargs)
        ttk.Frame.__init__(self, master, **options)
        self._finish()


class RoundedEntry(_Appearance, ttk.Entry):
    def __init__(self, master, **kwargs):
        options = self._prepare(master, "entry", kwargs)
        ttk.Entry.__init__(self, master, **options)
        self._finish()


class RoundedCombobox(_Appearance, ttk.Combobox):
    def __init__(self, master, **kwargs):
        options = self._prepare(master, "combo", kwargs)
        ttk.Combobox.__init__(self, master, **options)
        self._finish()


def set_rounded_scale(window, scale):
    """Border radius only; the existing DPI owner still scales fonts/padding."""
    if getattr(window, "_xiaochao_round_scale", None) == scale:
        return
    window._xiaochao_round_scale = scale
    def visit(widget):
        if isinstance(widget, tk.Toplevel) and widget is not window:
            return
        if isinstance(widget, _Appearance):
            widget._restyle()
        for child in widget.winfo_children():
            visit(child)
    visit(window)
