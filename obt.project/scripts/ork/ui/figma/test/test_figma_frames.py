#!/usr/bin/env ork.python
"""Figma frame viewer — displays each FRAME in data/design.json as a full-window
background, with a Next / Prev button (and left/right arrow keys, SPACE for next)
to cycle through them.

This is an orkid-only smoke test for the ork.ui.figma loader and SVG converter.
No impcore / imp_design dependencies; no real product content.

Run:
    ork.python test_figma_frames.py

Controls:
    Click Next/Prev     — cycle frames
    Arrow Right / Space — next
    Arrow Left          — previous
    C                   — toggle SHARP vs SOFT rendering (A/B comparison)
    Esc                 — quit
"""

import signal
from pathlib import Path

from orkengine.core import vec2, vec4, CrcStringProxy
from orkengine import lev2

from ork.ui import icon_library
from ork.ui.figma.design import FigmaDesign
from ork.ui.figma.converter import figma_node_to_svg

tokens = CrcStringProxy()

_THIS_DIR = Path(__file__).resolve().parent
_DESIGN_PATH = _THIS_DIR / "data" / "design.json"

# Layout constants for the Next/Prev overlay buttons.
BUTTON_W = 140
BUTTON_H = 48
BUTTON_MARGIN = 20
BUTTON_COLOR = vec4(0.15, 0.15, 0.18, 0.92)
BUTTON_COLOR_HOVER = vec4(0.30, 0.55, 0.95, 0.95)
STATUS_BAR_H = 40
STATUS_BAR_COLOR = vec4(0.08, 0.08, 0.10, 0.85)

# How often (in pixels of canvas width change) we re-rasterize the SVG.
# Bigger = fewer rsvg-convert invocations during drag-resize, but a blurrier
# image in between. 32px is a reasonable midpoint.
RETEX_STEP_PX = 32


class FigmaFrameViewer:

    def __init__(self):
        self.ezapp = lev2.OrkEzApp.create(self, width=1280, height=820,
                                          fullscreen=False)
        self.ezapp.setRefreshPolicy(lev2.RefreshFastest, 0)
        self.ezapp.topWidget.enableUiDraw()

        lg = self.ezapp.topLayoutGroup
        lg.clearColorStd = vec4(0.12, 0.12, 0.14, 1)

        cl = lg.makeChild(uiclass=lev2.ui.PrimCanvas, args=["frame_canvas"])
        self.canvas = cl.widget
        for edge in ("top", "left", "bottom", "right"):
            getattr(cl.layout, edge).anchorTo(getattr(lg.layout, edge))

        self.canvas.bg_color = vec4(0.12, 0.12, 0.14, 1)
        self.canvas.draw_background = True
        self.canvas.onUiEvent = self._on_event

        # Load design + collect frames
        if not _DESIGN_PATH.exists():
            raise SystemExit(f"missing {_DESIGN_PATH} — run sanitize.py first")
        self.design = FigmaDesign(_DESIGN_PATH)
        self.frames = self._collect_frames()
        if not self.frames:
            raise SystemExit(f"no frames found in {_DESIGN_PATH}")

        # Use the first frame's bbox as the canonical design size for
        # cover-mode layout.
        fw, fh = self.frames[0].width, self.frames[0].height
        self.frame_w = int(fw) if fw else 1728
        self.frame_h = int(fh) if fh else 1117

        self.current_idx = 0

        # Per-frame GPU state (populated in onGpuInit)
        self._bg_layer = None
        self._ui_layer = None
        self._text_layer = None
        self._ctx = None
        self._textures = {}        # frame_name -> lev2.Texture
        self._tex_render_w = 0     # width the textures are currently rasterized at
        # Press C to toggle between modes:
        #   "sharp": rasterize at on-screen draw size (1:1 texel→pixel)
        #   "soft":  rasterize at 2× then let GPU bilinear-downsample
        # Both paths share the same quad; only _ensure_textures_for_canvas reads it.
        self._render_mode = "sharp"
        self._bg_prim = None
        self._bg_quad = None
        self._next_quad = None
        self._prev_quad = None
        self._status_quad = None
        self._title_prim = None
        self._next_prim = None
        self._prev_prim = None
        self._last_canvas_size = (0, 0)
        self._next_hover = False
        self._prev_hover = False
        self._pressed = None  # "next" | "prev" | None

        def onCtrlC(signum, frame):
            self.ezapp.signalExit()
        signal.signal(signal.SIGINT, onCtrlC)

    # ------------------------------------------------------------------
    # Frame collection
    # ------------------------------------------------------------------

    def _collect_frames(self):
        """Walk pages → sections → frames; keep only FRAME nodes."""
        out = []
        for page in self.design.pages:
            for child in page.children:
                if child.type == "FRAME":
                    out.append(child)
                elif child.type == "SECTION":
                    for gc in child.children:
                        if gc.type == "FRAME":
                            out.append(gc)
        # Keep stable name-based order so "next" is deterministic.
        out.sort(key=lambda f: f.name)
        return out

    # ------------------------------------------------------------------
    # GPU init
    # ------------------------------------------------------------------

    def onGpuInit(self, ctx):
        self._ctx = ctx
        self.canvas.gpuInit(ctx)
        self.canvas.pipelineTextured.rasterstate.setBlendingMacro(tokens.ALPHA)

        self._bg_layer = self.canvas.createLayer("background")
        self._ui_layer = self.canvas.createLayer("ui")
        self._text_layer = self.canvas.createLayer("text")

        # Pre-render once at design resolution. We'll re-rasterize to match the
        # window's actual pixel size in _ensure_textures_for_canvas so glyphs
        # stay sharp instead of going through a GPU downscale.
        for frame in self.frames:
            tex = lev2.Texture(frame.name)
            self._textures[frame.name] = tex
        self._ensure_textures_for_canvas(self.canvas.width or 1280,
                                         self.canvas.height or 820)

        # Status bar (solid quad across top)
        self._status_quad = lev2.ui.QuadData()
        self._status_quad.setColor(STATUS_BAR_COLOR)
        status_prim = lev2.ui.QuadPrimitive(pipeline=self.canvas.pipelineSolid)
        status_prim.addQuad(self._status_quad)
        self._ui_layer.addPrimitive(status_prim)

        # Prev and Next button quads
        self._prev_quad = lev2.ui.QuadData()
        self._prev_quad.setColor(BUTTON_COLOR)
        prev_prim = lev2.ui.QuadPrimitive(pipeline=self.canvas.pipelineSolid)
        prev_prim.addQuad(self._prev_quad)
        self._ui_layer.addPrimitive(prev_prim)
        self._prev_prim = prev_prim

        self._next_quad = lev2.ui.QuadData()
        self._next_quad.setColor(BUTTON_COLOR)
        next_prim = lev2.ui.QuadPrimitive(pipeline=self.canvas.pipelineSolid)
        next_prim.addQuad(self._next_quad)
        self._ui_layer.addPrimitive(next_prim)
        self._next_prim = next_prim

        # Text overlays (title + button labels)
        self.font = lev2.FontManager.fontForId("i48")
        self.small_font = lev2.FontManager.fontForId("i24")

        self._title_prim = lev2.ui.TextPrimitive(font=self.small_font,
                                                 color=vec4(0.95, 0.95, 0.95, 1))
        self._text_layer.addPrimitive(self._title_prim)

        self._prev_label_prim = lev2.ui.TextPrimitive(font=self.small_font,
                                                     color=vec4(1, 1, 1, 1))
        self._text_layer.addPrimitive(self._prev_label_prim)

        self._next_label_prim = lev2.ui.TextPrimitive(font=self.small_font,
                                                     color=vec4(1, 1, 1, 1))
        self._text_layer.addPrimitive(self._next_label_prim)

        self._set_frame(0)

    # ------------------------------------------------------------------
    # On-demand retexturing (keeps glyphs crisp as the window resizes)
    # ------------------------------------------------------------------

    def _ensure_textures_for_canvas(self, canvas_w, canvas_h):
        """Re-render every frame SVG at the on-screen draw size.

        In cover mode the image's drawn width is `frame_w * scale` where
        `scale = max(cw/fw, ch/fh)`. Rasterizing at that width makes one
        texel = one pixel, so bilinear sampling doesn't blur edges.

        Snapped to RETEX_STEP_PX so resize drags don't hammer rsvg-convert.
        """
        if self._ctx is None or canvas_w <= 0 or canvas_h <= 0:
            return
        avail_h = max(1, int(canvas_h) - STATUS_BAR_H)
        fw, fh = self.frame_w, self.frame_h
        scale = max(canvas_w / fw, avail_h / fh)
        draw_w = max(512, int(round(fw * scale)))
        # "soft" mode rasterizes 2× bigger so the GPU has to bilinear-downsample —
        # that's the old (blurrier) behavior, kept for A/B comparison.
        if self._render_mode == "soft":
            draw_w *= 2
        # Snap to a step grid so jittery resize doesn't retrigger rasterization.
        target_w = (draw_w // RETEX_STEP_PX) * RETEX_STEP_PX
        if target_w == self._tex_render_w:
            return
        txi = self._ctx.TXI
        for frame in self.frames:
            svg = figma_node_to_svg(frame.raw)
            img = icon_library.from_svg_string_auto(svg, target_w)
            txi.updateTexture(self._textures[frame.name], img)
        self._tex_render_w = target_w

    # ------------------------------------------------------------------
    # Frame switching
    # ------------------------------------------------------------------

    def _set_frame(self, idx):
        if not self.frames:
            return
        self.current_idx = idx % len(self.frames)
        frame = self.frames[self.current_idx]
        tex = self._textures.get(frame.name)
        if tex is None:
            return

        # Rebuild the background quad primitive against the new texture
        # (QuadPrimitive.texture is read-only, so swap the primitive).
        if self._bg_prim is not None:
            self._bg_layer.removePrimitive(self._bg_prim)
        self._bg_prim = lev2.ui.QuadPrimitive(
            pipeline=self.canvas.pipelineTextured, texture=tex)
        self._bg_quad = lev2.ui.QuadData()
        self._bg_prim.addQuad(self._bg_quad)
        self._bg_layer.addPrimitive(self._bg_prim)

        # Full relayout (title text changes).
        cw = self.canvas.width or 1280
        ch = self.canvas.height or 820
        self._relayout(cw, ch)

    def _next(self):
        self._set_frame(self.current_idx + 1)

    def _prev(self):
        self._set_frame(self.current_idx - 1)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _relayout(self, cw, ch):
        """Full relayout — called on resize and frame change only."""
        cw_i = int(cw)
        ch_i = int(ch)

        # Retexture to the new pixel size if needed (keeps glyphs crisp).
        self._ensure_textures_for_canvas(cw_i, ch_i)

        # Background quad: cover the available area under the status bar.
        # In "sharp" mode we size the quad to match the rasterized texture
        # width (1:1 texel→pixel). In "soft" mode the texture is 2× the
        # natural draw size and we deliberately let the GPU downscale for
        # comparison — so we use the natural cover-mode draw size instead.
        avail_h = max(1, ch_i - STATUS_BAR_H)
        fw, fh = self.frame_w, self.frame_h
        if self._render_mode == "soft":
            scale = max(cw_i / fw, avail_h / fh)
            draw_w = int(round(fw * scale))
        else:
            draw_w = self._tex_render_w if self._tex_render_w > 0 else cw_i
        draw_h = int(round(draw_w * fh / fw))
        ox = (cw_i - draw_w) // 2
        oy = STATUS_BAR_H + (avail_h - draw_h) // 2

        if self._bg_quad is not None:
            self._bg_quad.setPosition(0, STATUS_BAR_H)
            self._bg_quad.setSize(cw_i, avail_h)
            u0 = -ox / draw_w
            u1 = (cw_i - ox) / draw_w
            v0 = -(oy - STATUS_BAR_H) / draw_h
            v1 = (ch_i - oy) / draw_h
            self._bg_quad.setUV(u0, v0, u1, v1)
            self._bg_quad.setColor(vec4(1, 1, 1, 1))

        # Status bar
        if self._status_quad is not None:
            self._status_quad.setPosition(0, 0)
            self._status_quad.setSize(cw_i, STATUS_BAR_H)

        # Prev / Next buttons: bottom-right corner, side by side.
        btn_y = ch_i - BUTTON_H - BUTTON_MARGIN
        next_x = cw_i - BUTTON_W - BUTTON_MARGIN
        prev_x = next_x - BUTTON_W - 10
        self._prev_rect = (prev_x, btn_y, BUTTON_W, BUTTON_H)
        self._next_rect = (next_x, btn_y, BUTTON_W, BUTTON_H)
        if self._prev_quad is not None:
            self._prev_quad.setPosition(prev_x, btn_y)
            self._prev_quad.setSize(BUTTON_W, BUTTON_H)
        if self._next_quad is not None:
            self._next_quad.setPosition(next_x, btn_y)
            self._next_quad.setSize(BUTTON_W, BUTTON_H)
        self._apply_button_hover()

        # Text placements
        title_text = ""
        if self.frames:
            frame = self.frames[self.current_idx]
            title_text = (f"[{self.current_idx + 1}/{len(self.frames)}]  "
                          f"{frame.name}    mode: {self._render_mode.upper()} "
                          f"(tex {self._tex_render_w}px — press C to toggle)")

        if self._title_prim is not None:
            self._title_prim.clearItems()
            self._title_prim.addItem(title_text, vec2(BUTTON_MARGIN, 8))

        if self._prev_label_prim is not None:
            self._prev_label_prim.clearItems()
            self._prev_label_prim.addItem("< Prev",
                                          vec2(prev_x + 30, btn_y + 14))
        if self._next_label_prim is not None:
            self._next_label_prim.clearItems()
            self._next_label_prim.addItem("Next >",
                                          vec2(next_x + 30, btn_y + 14))

        self.canvas.markDirty()

    def _toggle_render_mode(self):
        """Flip between the sharp (1:1) path and the soft (2× + downscale) path."""
        self._render_mode = "soft" if self._render_mode == "sharp" else "sharp"
        # Force a re-rasterization on the next _ensure call.
        self._tex_render_w = 0
        cw, ch = self.canvas.width, self.canvas.height
        if cw > 0 and ch > 0:
            self._relayout(cw, ch)

    def _apply_button_hover(self):
        """Only updates button colors — no position / UV / text changes,
        so the background texture isn't resampled on every mouse move."""
        if self._prev_quad is not None:
            self._prev_quad.setColor(BUTTON_COLOR_HOVER if self._prev_hover
                                     else BUTTON_COLOR)
        if self._next_quad is not None:
            self._next_quad.setColor(BUTTON_COLOR_HOVER if self._next_hover
                                     else BUTTON_COLOR)
        self.canvas.markDirty()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _hit(self, rect, x, y):
        rx, ry, rw, rh = rect
        return rx <= x <= rx + rw and ry <= y <= ry + rh

    def _on_event(self, ev):
        result = lev2.ui.HandlerResult()
        code = ev.code
        cw, ch = self.canvas.width, self.canvas.height

        if code == tokens.MOVE.hashed:
            n = self._hit(self._next_rect, ev.x, ev.y)
            p = self._hit(self._prev_rect, ev.x, ev.y)
            if n != self._next_hover or p != self._prev_hover:
                self._next_hover = n
                self._prev_hover = p
                self._apply_button_hover()
        elif code == tokens.PUSH.hashed:
            if self._hit(self._next_rect, ev.x, ev.y):
                self._pressed = "next"
            elif self._hit(self._prev_rect, ev.x, ev.y):
                self._pressed = "prev"
        elif code == tokens.RELEASE.hashed:
            pressed = self._pressed
            self._pressed = None
            if pressed == "next" and self._hit(self._next_rect, ev.x, ev.y):
                self._next()
            elif pressed == "prev" and self._hit(self._prev_rect, ev.x, ev.y):
                self._prev()
        elif code == tokens.KEY_DOWN.hashed:
            kc = ev.keycode
            print(f"[key] keycode={kc}")  # TEMP: identifies what key gave us kc
            # GLFW keys: RIGHT=262 LEFT=263 SPACE=32 ESC=256. On some builds
            # `keycode` is raw ASCII so we accept both capital and lowercase C.
            if kc in (262, 32):
                self._next()
            elif kc == 263:
                self._prev()
            elif kc == 256:
                self.ezapp.signalExit()
            elif kc in (67, 99):  # 'C' or 'c'
                self._toggle_render_mode()

        return result

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def onUpdate(self, updinfo):
        cw, ch = self.canvas.width, self.canvas.height
        if cw > 0 and ch > 0 and (cw, ch) != self._last_canvas_size:
            self._last_canvas_size = (cw, ch)
            self._relayout(cw, ch)

    def onUiEvent(self, uievent):
        return lev2.ui.HandlerResult()


if __name__ == "__main__":
    FigmaFrameViewer().ezapp.mainThreadLoop()
