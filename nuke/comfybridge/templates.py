# ComfyBridge (nuke-comfy-pipe) - by David Borrull
# https://github.com/dborrull/nuke-comfy-pipe
# MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
""".nk templates: pasting them creates nodes without Python Node objects
(Nuke Non-commercial lets Python touch only 10 per session)."""

import re

from . import __version__
from .help import TIPS
from .workflows import (IN_SRGB, IN_VIEWER, LIB_NONE, MAX_SLOTS, OUT_AUTO, OUT_COLOR, OUT_DATA,
                        SEND_COLOR, SEND_DATA)


def _nk_quote(s):
    return '"%s"' % str(s).replace("\\", "/").replace('"', "").replace("[", "\\[").replace("]", "\\]")


def _py(cmd):
    return _nk_quote("import comfy_bridge; comfy_bridge.%s(nuke.thisNode())" % cmd)


_AUX_INPUT = """ Input {
  inputs 0
  name aux
  number 2
  xpos 700
  ypos -450
 }
set N_aux [stack 0]
"""


def _slot_knobs(n):
    return """ addUserKnob {26 x%(n)d_div l "" +STARTLINE}
 addUserKnob {26 x%(n)d_title l "input %(n)d" +STARTLINE T "<i>not used by this workflow</i>"}
 addUserKnob {4 x%(n)d_from l from M {plate "aux input" "mask input"}}
 addUserKnob {41 x%(n)d_layer l layer -STARTLINE T CB_X%(n)dShuffle.in}
 addUserKnob {4 x%(n)d_send l send -STARTLINE M {%(color)s %(data)s}}
 addUserKnob {1 x%(n)d_node +INVISIBLE}
 addUserKnob {1 cb_x%(n)d_file +INVISIBLE}
 addUserKnob {6 cb_x%(n)d_ocio +INVISIBLE}
 addUserKnob {6 cb_x%(n)d_raw +INVISIBLE}
 addUserKnob {6 cb_x%(n)d_exp +INVISIBLE}
""" % {"n": n, "color": _nk_quote(SEND_COLOR), "data": _nk_quote(SEND_DATA)}


def _slot_nodes(n, W, H):
    """Per slot: pick the stream (plate / aux / mask) -> layer -> colour -> size of
    the plate (or working size) -> PNG or float EXR write."""
    x = 700 + 160 * n
    return """push $N_maskin
push $N_aux
push $N_in
 Switch {
  inputs 3
  which {{parent.x%(n)d_from}}
  name CB_X%(n)dFrom
  xpos %(x)d
  ypos -350
 }
 Shuffle {
  in rgba
  name CB_X%(n)dShuffle
  xpos %(x)d
  ypos -310
 }
 Multiply {
  channels rgb
  value {{"parent.cb_x%(n)d_exp ? pow(2,parent.send_exposure) : 1"}}
  name CB_X%(n)dExposure
  xpos %(x)d
  ypos -290
 }
 OCIODisplay {
  name CB_X%(n)dDisplay
  disable {{!parent.cb_x%(n)d_ocio}}
  xpos %(x)d
  ypos -270
 }
 Reformat {
  type "to box"
  box_width {{"parent.work_size>0 ? %(W)s : Input1.width"}}
  box_height {{"parent.work_size>0 ? %(H)s : Input1.height"}}
  box_fixed true
  resize distort
  filter Cubic
  name CB_X%(n)dResize
  xpos %(x)d
  ypos -230
 }
set N_x%(n)d [stack 0]
 Write {
  file "\\[value parent.cb_x%(n)d_file]"
  raw {{parent.cb_x%(n)d_raw}}
  file_type png
  datatype "8 bit"
  create_directories true
  name CB_X%(n)dWritePNG
  xpos %(x)d
  ypos -180
 }
push $N_x%(n)d
 Write {
  file "\\[value parent.cb_x%(n)d_file]"
  raw true
  file_type exr
  datatype "32 bit float"
  channels rgba
  create_directories true
  name CB_X%(n)dWriteEXR
  xpos %(x)d
  ypos -140
 }
""" % {"n": n, "x": x, "W": W, "H": H}


def _add_tooltips(text):
    """Insert t "tooltip" after the name/label of each user knob, as Nuke saves it."""
    def sub(m):
        tip = TIPS.get(m.group(2))
        return m.group(0) + (" t " + _nk_quote(tip) if tip else "")
    return re.sub(r'addUserKnob \{(\d+) (\w+)(?: l (?:"[^"]*"|[^\s}]+))?', sub, text)


def node_template(library_names, server):
    return _add_tooltips(_template_text(library_names, server))


def _template_text(library_names, server):
    lib = " ".join(_nk_quote(n) for n in [LIB_NONE] + list(library_names))
    W = "floor(Input1.width*parent.work_size/max(Input1.width,Input1.height)/32+0.5)*32"
    H = "floor(Input1.height*parent.work_size/max(Input1.width,Input1.height)/32+0.5)*32"
    return """set cut_paste_input [stack 0]
push $cut_paste_input
Group {
 name ComfyBridge1
 tile_color 0x6b4fa0ff
 label "\\[value cb_status]"
 knobChanged %(py_changed)s
 addUserKnob {20 comfy l ComfyBridge}
 addUserKnob {4 backend l "run on" M {"local ComfyUI" "Comfy Cloud"}}
 addUserKnob {1 server l server}
 server %(server)s
 addUserKnob {22 test l "test connection" -STARTLINE T %(py_test)s}
 addUserKnob {22 help l help -STARTLINE T %(py_help)s}
 addUserKnob {26 div_wf l workflow}
 addUserKnob {4 library l library M {%(lib)s}}
 addUserKnob {22 load l "load / reload" -STARTLINE T %(py_load)s}
 addUserKnob {2 workflow l "file (API .json)"}
 addUserKnob {26 wf_desc l "" +STARTLINE T " "}
 addUserKnob {26 models_info l models +STARTLINE T " "}
 addUserKnob {22 dl_models l "download missing models" -STARTLINE T %(py_models)s}
 addUserKnob {26 div_plate l plate}
 addUserKnob {3 first l "first frame"}
 first {{root.first_frame}}
 addUserKnob {3 last l "last frame" -STARTLINE}
 last {{root.last_frame}}
 addUserKnob {4 in_color l "input color" M {%(in_viewer)s %(in_srgb)s}}
 addUserKnob {7 send_exposure l "send exposure (stops)" R -4 4}
 addUserKnob {4 out_type l result M {%(out_auto)s %(out_data)s %(out_color)s}}
 addUserKnob {3 work_size l "working size"}
 work_size 0
 addUserKnob {2 out_dir l "output folder"}
 addUserKnob {6 versioning l "new version per run (v001, v002...)" +STARTLINE}
 versioning true
 addUserKnob {6 match_format l "match input format" +STARTLINE}
 match_format true
 addUserKnob {26 div_mask l mask}
 addUserKnob {4 mask_source l "mask from" M {"mask input" "plate"}}
 addUserKnob {4 mask_channels l send -STARTLINE M {alpha "rgba (as is)"}}
 addUserKnob {3 mask_grow l "grow (px)"}
 mask_grow 8
 addUserKnob {7 mask_soften l soften R 0 50}
 mask_soften 2
 addUserKnob {6 comp_over_plate l "comp result over plate with mask" +STARTLINE}
 comp_over_plate true
 addUserKnob {7 comp_feather l "comp feather" R 0 100}
 comp_feather 6
 addUserKnob {26 div_run l ""}
 addUserKnob {22 run l "<b>  Send to ComfyUI  </b>" +STARTLINE T %(py_run)s}
 addUserKnob {26 div_res l result}
 addUserKnob {6 auto_read l "create Read after each run" +STARTLINE}
 auto_read true
 addUserKnob {6 show_result l "show result in this node" +STARTLINE}
 show_result false
 addUserKnob {26 res_info l "" -STARTLINE T " "}
 addUserKnob {22 bake l "bake Read" +STARTLINE T %(py_bake)s}
 addUserKnob {26 ver l "" +STARTLINE T "<small>ComfyBridge v%(version)s  -  by David Borrull  -  github.com/dborrull/nuke-comfy-pipe</small>"}
 addUserKnob {22 repo l "open repo" -STARTLINE T %(py_repo)s}
 addUserKnob {20 inputs_tab l Inputs}
 addUserKnob {26 inputs_info l "" +STARTLINE T "<i>Extra images for the workflow (depth, a second pass, AOVs).<br>Take them from a layer of the plate, the aux input or the mask input.</i>"}
%(slot_knobs)s
 addUserKnob {1 cb_in_file +INVISIBLE}
 addUserKnob {1 cb_mask_file +INVISIBLE}
 addUserKnob {6 cb_mask_used +INVISIBLE}
 addUserKnob {6 cb_has_matte +INVISIBLE}
 addUserKnob {1 cb_out_file +INVISIBLE}
 addUserKnob {3 cb_first +INVISIBLE}
 cb_first 1
 addUserKnob {3 cb_last +INVISIBLE}
 cb_last 1
 addUserKnob {6 cb_has_result +INVISIBLE}
 addUserKnob {6 cb_in_ocio +INVISIBLE}
 addUserKnob {6 cb_in_raw +INVISIBLE}
 addUserKnob {6 cb_out_ocio +INVISIBLE}
 addUserKnob {6 cb_out_raw +INVISIBLE}
 addUserKnob {1 cb_status +INVISIBLE}
 addUserKnob {1 load_id +INVISIBLE}
 addUserKnob {1 save_id +INVISIBLE}
 addUserKnob {1 param_map +INVISIBLE}
 addUserKnob {1 wf_loaded +INVISIBLE}
 addUserKnob {1 cs_state +INVISIBLE}
 addUserKnob {7 cb_exp_used +INVISIBLE}
}
 Input {
  inputs 0
  name Input1
  xpos 0
  ypos -400
 }
set N_in [stack 0]
 Multiply {
  channels rgb
  value {{pow(2,parent.send_exposure)}}
  name CB_InExposure
  xpos 0
  ypos -370
 }
 OCIODisplay {
  name CB_InDisplay
  disable {{!parent.cb_in_ocio}}
  xpos 0
  ypos -340
 }
 Reformat {
  type "to box"
  box_width {{"%(W)s"}}
  box_height {{"%(H)s"}}
  box_fixed true
  resize distort
  filter Cubic
  name CB_InResize
  disable {{parent.work_size<=0}}
  xpos 0
  ypos -300
 }
 Write {
  file "\\[value parent.cb_in_file]"
  raw {{parent.cb_in_raw}}
  file_type png
  datatype "8 bit"
  create_directories true
  name CB_Write
  xpos 0
  ypos -250
 }
 Input {
  inputs 0
  name mask
  number 1
  xpos 400
  ypos -400
 }
set N_maskraw [stack 0]
 Reformat {
  type "to box"
  box_width {{Input1.width}}
  box_height {{Input1.height}}
  box_fixed true
  resize fit
  filter Cubic
  name CB_MaskFit
  xpos 400
  ypos -375
 }
set N_maskin [stack 0]
push $N_in
push $N_maskin
 Switch {
  inputs 2
  which {{parent.mask_source}}
  name CB_MaskSource
  xpos 400
  ypos -350
 }
 Shuffle {
  red alpha
  green alpha
  blue alpha
  alpha alpha
  name CB_MaskToRGBA
  disable {{parent.mask_channels}}
  xpos 400
  ypos -310
 }
 FilterErode {
  channels rgba
  size {{-parent.mask_grow}}
  name CB_MaskGrow
  xpos 400
  ypos -270
 }
set N_maskgrown [stack 0]
 Blur {
  channels rgba
  size {{parent.mask_soften}}
  name CB_MaskSoften
  xpos 400
  ypos -230
 }
 Reformat {
  type "to box"
  box_width {{"%(W)s"}}
  box_height {{"%(H)s"}}
  box_fixed true
  resize distort
  filter Cubic
  name CB_MaskResize
  disable {{parent.work_size<=0}}
  xpos 400
  ypos -190
 }
 Write {
  file "\\[value parent.cb_mask_file]"
  raw true
  file_type png
  datatype "8 bit"
  create_directories true
  name CB_WriteMask
  xpos 400
  ypos -150
 }
%(slots)spush $N_maskgrown
 Blur {
  channels rgba
  size {{parent.comp_feather}}
  name CB_CompFeather
  xpos 550
  ypos -230
 }
set N_compmask [stack 0]
 Read {
  inputs 0
  raw {{parent.cb_out_raw}}
  on_error black
  name CB_Read
  xpos 200
  ypos -340
 }
 OCIODisplay {
  invert true
  name CB_OutDisplay
  disable {{!parent.cb_out_ocio}}
  xpos 200
  ypos -280
 }
 Multiply {
  channels rgb
  value {{pow(2,-parent.cb_exp_used)}}
  name CB_OutExposure
  xpos 200
  ypos -260
 }
 Reformat {
  type "to box"
  box_width {{Input1.width}}
  box_height {{Input1.height}}
  box_fixed true
  resize distort
  filter Cubic
  name CB_OutResize
  disable {{!parent.match_format}}
  xpos 200
  ypos -240
 }
set N_res [stack 0]
push $N_compmask
push $N_res
push $N_in
 Keymix {
  inputs 3
  maskChannel rgba.alpha
  name CB_Keymix
  xpos 200
  ypos -190
 }
push $N_res
 Switch {
  inputs 2
  which {{"parent.comp_over_plate && parent.cb_mask_used"}}
  name CB_CompSwitch
  xpos 200
  ypos -150
 }
set N_comp [stack 0]
push $N_res
push $N_comp
 Copy {
  inputs 2
  from0 rgba.alpha
  to0 rgba.alpha
  name CB_MatteToAlpha
  disable {{!parent.cb_has_matte}}
  xpos 200
  ypos -110
 }
push $N_in
 Switch {
  inputs 2
  which {{"parent.cb_has_result && parent.show_result"}}
  name CB_Switch
  xpos 0
  ypos -160
 }
 Output {
  name Output1
  xpos 0
  ypos -100
 }
end_group
""" % {
        "server": _nk_quote(server), "lib": lib, "W": W, "H": H, "version": __version__,
        "py_test": _py("test_connection"), "py_load": _py("load_workflow_into"),
        "py_run": _py("run"), "py_repo": _py("open_repo"), "py_changed": _nk_quote("import comfy_bridge; comfy_bridge.knob_changed(nuke.thisNode(), nuke.thisKnob())"), "py_bake": _py("bake_read"), "py_help": _py("show_help"), "py_models": _py("download_models"),
        "in_viewer": _nk_quote(IN_VIEWER), "in_srgb": _nk_quote(IN_SRGB),
        "out_auto": _nk_quote(OUT_AUTO), "out_data": _nk_quote(OUT_DATA),
        "out_color": _nk_quote(OUT_COLOR),
        "slot_knobs": "".join(_slot_knobs(n) for n in range(1, MAX_SLOTS + 1)),
        "slots": _AUX_INPUT + "".join(_slot_nodes(n, W, H) for n in range(1, MAX_SLOTS + 1)),
    }


def bake_template(path, first, last, raw, colorspace, dv_ocio,
                  base="ComfyBridgeResult", xpos=None, ypos=None, label="", fmt=None, exposure=0.0):
    """Return (.nk text, {node name: (x, y)}). Positions are applied after the
    paste, because Nuke re-places pasted nodes next to the current selection."""
    pos = {}
    text = """Read {
 inputs 0
 file %s
 first %d
 last %d
 origfirst %d
 origlast %d
 on_error black
%s label %s
 name %s_Read
}
""" % (_nk_quote(path), first, last, first, last,
       " raw true\n" if raw else (" colorspace %s\n" % _nk_quote(colorspace) if colorspace else ""),
       _nk_quote(label), base)
    order = [base + "_Read"]
    if dv_ocio:
        text += """OCIODisplay {
 colorspace %s
 display %s
 view %s
 invert true
 name %s_Inverse
}
""" % (_nk_quote(dv_ocio[2]), _nk_quote(dv_ocio[0]), _nk_quote(dv_ocio[1]), base)
        order.append(base + "_Inverse")
    if exposure:
        text += """Multiply {
 channels rgb
 value %g
 label "send exposure %+g"
 name %s_Exposure
}
""" % (2.0 ** -float(exposure), float(exposure), base)
        order.append(base + "_Exposure")
    if fmt:
        text += """Reformat {
 type "to box"
 box_width %d
 box_height %d
 box_fixed true
 resize distort
 filter Cubic
 name %s_Reformat
}
""" % (fmt[0], fmt[1], base)
        order.append(base + "_Reformat")
    if xpos is not None:
        for i, n in enumerate(order):
            pos[n] = (xpos, ypos + (0 if i == 0 else 60 + 40 * i))
    return text, pos
