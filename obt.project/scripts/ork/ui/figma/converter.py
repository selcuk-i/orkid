"""
ork.ui.figma.converter — Deterministic Figma JSON node → SVG converter.

Converts Figma node trees (as parsed from the Figma REST API JSON) into
SVG markup.  Handles the node types used in card-based UI designs:

  FRAME, RECTANGLE, VECTOR (ellipse), TEXT, BOOLEAN_OPERATION, INSTANCE, GROUP

When the JSON is fetched with geometry=paths, fillGeometry SVG path data
is used for pixel-perfect rendering.  Otherwise, bounding-box fallbacks
are used.
"""


def _figma_color_to_hex(color_dict):
    """Convert Figma color dict {r, g, b, a} to '#RRGGBB'."""
    r = int(color_dict.get("r", 0) * 255)
    g = int(color_dict.get("g", 0) * 255)
    b = int(color_dict.get("b", 0) * 255)
    return f"#{r:02x}{g:02x}{b:02x}"


def _get_solid_fill(node):
    """Return the first visible SOLID fill hex color, or None."""
    for fill in node.get("fills", []):
        if fill.get("type") == "SOLID" and fill.get("visible", True):
            return _figma_color_to_hex(fill["color"])
    return None


def _get_solid_stroke(node):
    """Return the first visible SOLID stroke hex color, or None."""
    for stroke in node.get("strokes", []):
        if stroke.get("type") == "SOLID" and stroke.get("visible", True):
            return _figma_color_to_hex(stroke["color"])
    return None


_stroke_clip_counter = [0]

def _render_stroke(node, ox, oy, stroke_color):
    """Render a node's stroke as an SVG stroke on its fillGeometry path.

    For strokeAlign=INSIDE: uses double stroke-width clipped to the shape.
    For strokeAlign=CENTER (default): uses stroke-width as-is.
    """
    fill_geom = node.get("fillGeometry", [])
    if not fill_geom:
        return None

    stroke_weight = node.get("strokeWeight", 1)
    stroke_align = node.get("strokeAlign", "CENTER")
    transform = _compute_transform(node, ox, oy)

    parts = []
    for geom in fill_geom:
        path_data = geom.get("path", "")
        if not path_data:
            continue

        if stroke_align == "INSIDE":
            clip_id = f"sc{_stroke_clip_counter[0]}"
            _stroke_clip_counter[0] += 1
            parts.append(
                f'<defs><clipPath id="{clip_id}">'
                f'<path d="{path_data}" transform="{transform}"/>'
                f'</clipPath></defs>'
                f'<g clip-path="url(#{clip_id})">'
                f'<path d="{path_data}" fill="none" stroke="{stroke_color}" '
                f'stroke-width="{stroke_weight * 2}" '
                f'transform="{transform}"/>'
                f'</g>'
            )
        else:
            parts.append(
                f'<path d="{path_data}" fill="none" stroke="{stroke_color}" '
                f'stroke-width="{stroke_weight}" '
                f'transform="{transform}"/>'
            )

    return "\n".join(parts) if parts else None


def _svg_drop_shadow(effect, filter_id="shadow"):
    """Convert a Figma DROP_SHADOW effect to an SVG filter definition."""
    offset = effect.get("offset", {})
    dx = offset.get("x", 0)
    dy = offset.get("y", 0)
    radius = effect.get("radius", 0)
    color = effect.get("color", {})
    opacity = color.get("a", 0.25)
    return (
        f'<filter id="{filter_id}" x="-20%" y="-20%" width="140%" height="150%">'
        f'<feDropShadow dx="{dx}" dy="{dy}" stdDeviation="{radius / 2}" '
        f'flood-color="{_figma_color_to_hex(color)}" flood-opacity="{opacity}"/>'
        f'</filter>'
    )


# -------------------------------------------------------------------------
# Shape rendering — uses fillGeometry paths when available
# -------------------------------------------------------------------------

def _render_stroke_geometry(node, ox, oy, stroke_color):
    """Render a node's strokeGeometry as filled SVG paths.

    strokeGeometry contains the pre-computed stroke outlines as filled shapes.
    Used for stroke-only nodes (no fillGeometry) like line arrows.
    Applies rotation via the node's relativeTransform matrix.
    """
    stroke_geom = node.get("strokeGeometry", [])
    if not stroke_geom:
        return None

    transform = _compute_transform(node, ox, oy)

    paths = []
    for geom in stroke_geom:
        path_data = geom.get("path", "")
        if path_data:
            wind = geom.get("windingRule", "NONZERO")
            fill_rule = "evenodd" if wind == "EVENODD" else "nonzero"
            paths.append(
                f'<path d="{path_data}" fill="{stroke_color}" '
                f'fill-rule="{fill_rule}" '
                f'transform="{transform}"/>'
            )
    return "\n".join(paths) if paths else None


def _compute_transform(node, ox, oy):
    """Compute the SVG transform for a Figma node.

    For non-rotated nodes: simple translate from absoluteBoundingBox.
    For rotated nodes: uses the absoluteBoundingBox position but applies
    rotation around the local center (size/2, size/2), matching how Figma
    stores fillGeometry in local pre-rotation space.
    """
    import math
    bbox = node.get("absoluteBoundingBox") or {}
    bx = bbox.get("x", 0) - ox
    by = bbox.get("y", 0) - oy
    bw = bbox.get("width", 0)
    bh = bbox.get("height", 0)

    rotation = node.get("rotation", 0)
    if abs(rotation) < 0.001:
        return f"translate({bx},{by})"

    # Node has rotation. fillGeometry is in local (unrotated) space.
    # The absoluteBoundingBox is the AABB of the rotated shape.
    # We need to: translate to AABB center, rotate, translate back by half the
    # *unrotated* size. The unrotated size comes from the 'size' field.
    size = node.get("size") or {}
    lw = size.get("x", bw)
    lh = size.get("y", bh)

    # AABB center
    cx = bx + bw / 2
    cy = by + bh / 2

    # SVG: translate to center, rotate, translate back by half local size
    rot_deg = math.degrees(rotation)
    return f"translate({cx},{cy}) rotate({rot_deg}) translate({-lw/2},{-lh/2})"


def _render_fill_geometry(node, ox, oy, fill):
    """Render a node using its fillGeometry SVG path data.

    Returns SVG string if fillGeometry is present, else None.
    """
    fill_geom = node.get("fillGeometry", [])
    if not fill_geom:
        return None

    transform = _compute_transform(node, ox, oy)

    paths = []
    for geom in fill_geom:
        path_data = geom.get("path", "")
        if path_data:
            wind = geom.get("windingRule", "NONZERO")
            fill_rule = "evenodd" if wind == "EVENODD" else "nonzero"
            paths.append(
                f'<path d="{path_data}" fill="{fill}" '
                f'fill-rule="{fill_rule}" '
                f'transform="{transform}"/>'
            )
    return "\n".join(paths) if paths else None


def _convert_shape(node, ox, oy, fill_override=None):
    """Convert a shape node (RECTANGLE, VECTOR, ELLIPSE) to SVG.

    Prefers fillGeometry paths when available for exact rendering.
    Falls back to bounding-box approximations otherwise.
    """
    fill = fill_override or _get_solid_fill(node)
    if fill is None:
        return ""

    # Prefer fillGeometry for all shape types
    svg = _render_fill_geometry(node, ox, oy, fill)
    if svg:
        return svg

    # Fallback: bounding-box based rendering
    node_type = node.get("type", "")
    bbox = node["absoluteBoundingBox"]
    x = bbox["x"] - ox
    y = bbox["y"] - oy
    w = bbox["width"]
    h = bbox["height"]

    if node_type == "RECTANGLE":
        r = node.get("cornerRadius", 0)
        rot = node.get("rotation", 0)
        attrs = f'x="{x}" y="{y}" width="{w}" height="{h}"'
        if r:
            attrs += f' rx="{r}" ry="{r}"'
        attrs += f' fill="{fill}"'
        if abs(rot) > 0.001:
            cx = x + w / 2
            cy = y + h / 2
            return f'<rect {attrs} transform="rotate({-rot} {cx} {cy})"/>'
        return f'<rect {attrs}/>'

    else:  # VECTOR, ELLIPSE
        cx = x + w / 2
        cy = y + h / 2
        rx = w / 2
        ry = h / 2
        return f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{fill}"/>'


# -------------------------------------------------------------------------
# Text rendering
# -------------------------------------------------------------------------

def _wrap_text(text, box_width, font_size):
    """Word-wrap text to fit within box_width using estimated character widths.

    Average character width is approximately 0.6 * fontSize for proportional fonts.
    """
    return [line for line, _start in _wrap_text_indexed(text, box_width, font_size)]


def _wrap_text_indexed(text, box_width, font_size, char_width_ratio=None):
    """Like _wrap_text but returns [(line_text, start_index_in_original)].

    The start index points into `text` (not stripped or modified) and is used
    by per-character style overrides to slice the right weight runs into each
    wrapped line. `char_width_ratio` overrides the module-level
    TEXT_CHAR_WIDTH_RATIO for this one call — used by callers that know a
    specific TEXT node wants a different wrap density (e.g. per-node
    override from editor.json)."""
    if char_width_ratio is None:
        from ork.ui.figma.constants import TEXT_CHAR_WIDTH_RATIO
        char_width_ratio = TEXT_CHAR_WIDTH_RATIO
    char_width = font_size * char_width_ratio
    max_chars = max(1, int(box_width / char_width))

    wrapped = []
    # Walk the original text in one pass so we can emit correct
    # start-indices for every line we produce.
    pos = 0
    for paragraph in text.split("\n"):
        if not paragraph:
            wrapped.append(("", pos))
            pos += 1  # for the \n we just consumed
            continue
        words = paragraph.split(" ")
        current_line = ""
        current_line_start = pos
        word_pos = pos
        for wi, word in enumerate(words):
            test = f"{current_line} {word}".strip() if current_line else word
            if len(test) > max_chars and current_line:
                wrapped.append((current_line, current_line_start))
                current_line = word
                current_line_start = word_pos
            else:
                current_line = test
            word_pos += len(word) + 1  # +1 for the space separator
        if current_line:
            wrapped.append((current_line, current_line_start))
        pos += len(paragraph) + 1  # +1 for the \n separator (may overshoot on last; harmless)
    return wrapped


def _convert_text(node, ox, oy):
    """Convert a Figma TEXT node to SVG text element with word wrapping.

    Honors Figma's per-character rich-text overrides
    (characterStyleOverrides + styleOverrideTable) by emitting <tspan>
    runs inside each wrapped line when individual character ranges have
    a different fontWeight (or other style) than the node's base style."""
    bbox = node.get("absoluteBoundingBox") or {}
    x = bbox.get("x", 0) - ox
    y = bbox.get("y", 0) - oy
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)
    fill = _get_solid_fill(node) or "#FFFFFF"
    text = node.get("characters", "")
    style = node.get("style", {})
    font_family = style.get("fontFamily", "sans-serif")
    font_size = style.get("fontSize", 16)
    font_weight = style.get("fontWeight", 400)
    text_case = style.get("textCase")
    if text_case == "UPPER":
        text = text.upper()
    elif text_case == "LOWER":
        text = text.lower()
    elif text_case == "TITLE":
        text = text.title()
    h_align = style.get("textAlignHorizontal", "LEFT")
    v_align = style.get("textAlignVertical", "TOP")

    if h_align == "CENTER":
        tx = x + w / 2
        anchor = "middle"
    elif h_align == "RIGHT":
        tx = x + w
        anchor = "end"
    else:
        tx = x
        anchor = "start"

    from ork.ui.figma.constants import TEXT_ASCENDER_RATIO, LINE_HEIGHT_MULTIPLIER

    weight_attr = f' font-weight="{font_weight}"' if font_weight != 400 else ''
    common = (f'text-anchor="{anchor}" '
              f'font-family="{font_family}, sans-serif" font-size="{font_size}"'
              f'{weight_attr} fill="{fill}"')

    # Per-character style overrides (Figma rich text). If present, build a
    # char_weights array (same length as `text`) where each entry is the
    # effective fontWeight for that character. Same for fill overrides.
    char_weights, char_fills = _build_char_style_arrays(node, text, font_weight, fill)

    # Word-wrap. Keep start indices so we can slice the override arrays.
    # A TEXT node can opt out of the frame-wide char-width ratio via a
    # per-node override (editor.json `overrides[nid].TEXT_CHAR_WIDTH_RATIO`);
    # _apply_overrides stashes it on the node when it's loaded.
    node_ratio = node.get("_text_char_width_ratio_override")
    if w > 0:
        wrapped = _wrap_text_indexed(text, w, font_size,
                                     char_width_ratio=node_ratio)
    else:
        wrapped = []
        pos = 0
        for p in text.split("\n"):
            wrapped.append((p, pos))
            pos += len(p) + 1

    # Resolve vertical anchor. `ty` is the baseline of the FIRST emitted
    # line. For CENTER / BOTTOM we need to know the total text block
    # height, which depends on the post-wrap line count (and must include
    # the trailing blank paragraphs otherwise textAlignVertical=BOTTOM
    # on a paragraph with trailing `\n\n` would ignore the trailing
    # whitespace and pull subsequent lines above the intended baseline).
    ascender = font_size * TEXT_ASCENDER_RATIO
    line_h = font_size * LINE_HEIGHT_MULTIPLIER
    n_lines = max(1, len(wrapped))
    block_h = (n_lines - 1) * line_h + font_size  # visual height of wrapped block
    if v_align == "CENTER":
        ty = y + (h - block_h) / 2 + ascender
    elif v_align == "BOTTOM":
        # Anchor the LAST visible line's BASELINE so the text block ends
        # flush with the bbox bottom. Previously this computed ty for the
        # FIRST line at the bbox bottom, then added `i * line_h` DOWN —
        # multi-line BOTTOM-anchored text spilled below the bbox.
        ty = y + h - (font_size - ascender) - (n_lines - 1) * line_h
    else:  # TOP
        ty = y + ascender

    parts = []
    for i, (line, start) in enumerate(wrapped):
        if not line:
            continue
        line_y = ty + i * line_h
        if char_weights is None and char_fills is None:
            parts.append(f'<text x="{tx}" y="{line_y}" {common}>'
                         f'{_svg_escape(line)}</text>')
        else:
            tspans = _render_tspans(line, start, char_weights, char_fills,
                                    font_weight, fill)
            parts.append(f'<text x="{tx}" y="{line_y}" {common}>'
                         f'{tspans}</text>')
    return "\n".join(parts)


def _build_char_style_arrays(node, text, base_weight, base_fill):
    """Build per-character arrays from Figma's characterStyleOverrides +
    styleOverrideTable. Returns (weights, fills) each either None (no
    per-char override active) or a list len(text) of effective values."""
    cso = node.get("characterStyleOverrides") or []
    table = node.get("styleOverrideTable") or {}
    if not cso or not table:
        return None, None

    weights_differ = False
    fills_differ = False
    weights = [base_weight] * len(text)
    fills = [base_fill] * len(text)

    # Figma pads cso to len(characters) but may have trailing default 0s;
    # iterate up to min length to be safe.
    n = min(len(cso), len(text))
    for i in range(n):
        key = cso[i]
        if not key:
            continue
        ov = table.get(str(key))
        if not ov:
            continue
        if "fontWeight" in ov:
            fw = ov.get("fontWeight")
            if fw != base_weight:
                weights[i] = fw
                weights_differ = True
        # Character-level fill override (Figma exposes this as 'fills').
        for f in ov.get("fills") or []:
            if f.get("type") == "SOLID" and f.get("visible", True):
                c = f.get("color") or {}
                hx = _figma_color_to_hex(c)
                if hx != base_fill:
                    fills[i] = hx
                    fills_differ = True

    return (weights if weights_differ else None,
            fills if fills_differ else None)


def _render_tspans(line, line_start_idx, char_weights, char_fills,
                   base_weight, base_fill):
    """Render a single wrapped line as a sequence of <tspan> runs, grouping
    contiguous characters that share the same override state."""
    out = []
    i = 0
    n = len(line)
    while i < n:
        abs_i = line_start_idx + i
        w_here = (char_weights[abs_i]
                  if char_weights is not None and abs_i < len(char_weights)
                  else base_weight)
        f_here = (char_fills[abs_i]
                  if char_fills is not None and abs_i < len(char_fills)
                  else base_fill)
        j = i + 1
        while j < n:
            abs_j = line_start_idx + j
            w2 = (char_weights[abs_j]
                  if char_weights is not None and abs_j < len(char_weights)
                  else base_weight)
            f2 = (char_fills[abs_j]
                  if char_fills is not None and abs_j < len(char_fills)
                  else base_fill)
            if w2 != w_here or f2 != f_here:
                break
            j += 1
        run = _svg_escape(line[i:j])
        attrs = []
        if w_here != base_weight:
            attrs.append(f'font-weight="{w_here}"')
        if f_here != base_fill:
            attrs.append(f'fill="{f_here}"')
        if attrs:
            out.append(f'<tspan {" ".join(attrs)}>{run}</tspan>')
        else:
            out.append(run)
        i = j
    return "".join(out)


def _svg_escape(text):
    """Escape special XML characters in text content."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# -------------------------------------------------------------------------
# Boolean operations
# -------------------------------------------------------------------------

_bool_id_counter = [0]

def _convert_boolean_operation(node, ox, oy):
    """Convert a Figma BOOLEAN_OPERATION (UNION) to SVG.

    Strategy: render all child shapes into a clipPath, then fill through
    the clip. This produces a true boolean union with no seams.

    When fillGeometry is available on the BOOLEAN_OPERATION node itself,
    we use that directly (it's the pre-computed union outline).
    """
    fill = _get_solid_fill(node)
    if fill is None:
        return ""

    # If the boolean op node itself has fillGeometry, use it directly
    # (this is the computed union as SVG paths)
    fg = node.get("fillGeometry", [])
    if fg:
        transform = _compute_transform(node, ox, oy)
        paths = []
        for geom in fg:
            path_data = geom.get("path", "")
            if path_data:
                wind = geom.get("windingRule", "NONZERO")
                fill_rule = "evenodd" if wind == "EVENODD" else "nonzero"
                paths.append(
                    f'<path d="{path_data}" fill="{fill}" '
                    f'fill-rule="{fill_rule}" '
                    f'transform="{transform}"/>'
                )
        if paths:
            return "\n".join(paths)

    # Fallback: clipPath approach from child shapes
    clip_parts = []
    for child in node.get("children", []):
        child_type = child.get("type", "")
        if child_type in ("GROUP", "INSTANCE"):
            for grandchild in child.get("children", []):
                clip_parts.append(_convert_shape(grandchild, ox, oy, fill_override="white"))
        else:
            clip_parts.append(_convert_shape(child, ox, oy, fill_override="white"))

    clip_svg = "\n".join(p for p in clip_parts if p)
    if not clip_svg:
        return ""

    clip_id = f"bool{_bool_id_counter[0]}"
    _bool_id_counter[0] += 1

    bbox = node["absoluteBoundingBox"]
    x = bbox["x"] - ox
    y = bbox["y"] - oy
    w = bbox["width"]
    h = bbox["height"]
    pad = 2
    rx, ry, rw, rh = x - pad, y - pad, w + pad * 2, h + pad * 2

    return (
        f'<defs><clipPath id="{clip_id}">{clip_svg}</clipPath></defs>'
        f'<rect x="{rx}" y="{ry}" width="{rw}" height="{rh}" '
        f'fill="{fill}" clip-path="url(#{clip_id})"/>'
    )


# -------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------

def figma_node_to_svg(node, width=None, height=None):
    """Convert a Figma node tree to a complete SVG document string."""
    bbox = node["absoluteBoundingBox"]
    ox = bbox["x"]
    oy = bbox["y"]
    w = width or bbox["width"]
    h = height or bbox["height"]

    # Collect filter definitions from effects and compute shadow padding
    defs_parts = []
    filter_id_counter = [0]
    shadow_pad = [0]  # max padding needed for drop shadows

    def _collect_effects(n):
        for effect in n.get("effects", []):
            if effect.get("type") == "DROP_SHADOW" and effect.get("visible", True):
                fid = f"shadow{filter_id_counter[0]}"
                filter_id_counter[0] += 1
                defs_parts.append(_svg_drop_shadow(effect, fid))
                n["_svg_filter_id"] = fid
                # Compute padding: blur radius * 2 + offset
                offset = effect.get("offset", {})
                radius = effect.get("radius", 0)
                dx = abs(offset.get("x", 0))
                dy = abs(offset.get("y", 0))
                pad = int(radius + max(dx, dy) + 2)
                shadow_pad[0] = max(shadow_pad[0], pad)
        for child in n.get("children", []):
            _collect_effects(child)

    _collect_effects(node)

    defs_svg = ""
    if defs_parts:
        defs_svg = "<defs>" + "".join(defs_parts) + "</defs>"

    body_parts = []
    _render_children(node, ox, oy, body_parts)
    body_svg = "\n".join(body_parts)

    # Expand viewBox to accommodate drop shadow overflow
    pad = shadow_pad[0]
    vb_x = -pad
    vb_y = -pad
    vb_w = w + pad * 2
    vb_h = h + pad * 2

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb_x} {vb_y} {vb_w} {vb_h}">'
        f'{defs_svg}'
        f'{body_svg}'
        f'</svg>'
    )


def figma_children_to_svg_fragment(node, ox, oy):
    """Convert a node's children to SVG fragment (no <svg> wrapper)."""
    parts = []
    _render_children(node, ox, oy, parts)
    return "\n".join(parts)


def _render_children(node, ox, oy, parts):
    """Recursively render a node's children into SVG parts list."""
    for child in node.get("children", []):
        _render_node(child, ox, oy, parts)


def _render_node(node, ox, oy, parts):
    """Render a single Figma node to SVG and append to parts."""
    node_type = node.get("type", "")
    visible = node.get("visible", True)
    if not visible:
        return

    filter_attr = ""
    if "_svg_filter_id" in node:
        filter_attr = f' filter="url(#{node["_svg_filter_id"]})"'

    if node_type in ("RECTANGLE", "VECTOR", "ELLIPSE"):
        shape_parts = []

        fill = _get_solid_fill(node)
        stroke = _get_solid_stroke(node)

        if fill is not None:
            # Has a fill color — render the fill
            fg_svg = _render_fill_geometry(node, ox, oy, fill)
            if fg_svg:
                shape_parts.append(fg_svg)
            else:
                svg = _convert_shape(node, ox, oy)
                if svg:
                    shape_parts.append(svg)

        if stroke is not None:
            # Try fillGeometry-based stroke first, then strokeGeometry
            sg_svg = _render_stroke(node, ox, oy, stroke)
            if sg_svg:
                shape_parts.append(sg_svg)
            else:
                # Stroke-only nodes (e.g. line arrows) — use strokeGeometry
                sg2 = _render_stroke_geometry(node, ox, oy, stroke)
                if sg2:
                    shape_parts.append(sg2)

        if shape_parts:
            combined = "\n".join(shape_parts)
            if filter_attr:
                parts.append(f'<g{filter_attr}>{combined}</g>')
            else:
                parts.append(combined)

    elif node_type == "TEXT":
        parts.append(_convert_text(node, ox, oy))

    elif node_type == "BOOLEAN_OPERATION":
        parts.append(_convert_boolean_operation(node, ox, oy))

    elif node_type in ("FRAME", "GROUP", "INSTANCE", "SECTION"):
        fill = _get_solid_fill(node)
        if fill is not None:
            bbox = node["absoluteBoundingBox"]
            x = bbox["x"] - ox
            y = bbox["y"] - oy
            w = bbox["width"]
            h = bbox["height"]
            r = node.get("cornerRadius", 0)
            attrs = f'x="{x}" y="{y}" width="{w}" height="{h}"'
            if r:
                attrs += f' rx="{r}" ry="{r}"'
            attrs += f' fill="{fill}"{filter_attr}'
            parts.append(f'<rect {attrs}/>')
        _render_children(node, ox, oy, parts)
