# ComfyBridge (nuke-comfy-pipe) - by David Borrull
# https://github.com/dborrull/nuke-comfy-pipe
# MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
"""Help texts: knob tooltips, parameter tooltips and the quick guide."""

from . import __version__

REPO_URL = "https://github.com/dborrull/nuke-comfy-pipe"

PARAM_TIPS_LABEL = {
 "prompt": "What the image should be. Describe it as a photograph: place, materials, time of day, light, mood. The depth / mask keep the layout; the prompt decides the look.",
 "positive": "What the image should be. Describe it as a photograph: place, materials, light, mood.",
 "prompt +": "What the image should be. Describe it as a photograph: place, materials, light, mood.",
 "negative": "What to avoid (CG look, cartoon, oversaturation, people...). Only works with cfg above 1: with Lightning LoRAs at cfg 1 it has no effect.",
 "prompt -": "What to avoid. Only works with cfg above 1.",
 "instruction": "An instruction for the edit model: what to change and what to keep. Refer to the inputs as image 1, image 2, image 3.",
}
PARAM_TIPS = {
 "denoise": "How much the image may change. 0 = returns the input, 1 = completely new. img2img: 0.6 keeps the look of the input, 0.8-0.95 reinvents textures (depth or mask keep the layout).",
 "seed": "Random seed. Same seed + same settings = same result. Change it to get a variation.",
 "noise_seed": "Random seed. Same seed + same settings = same result. Change it to get a variation.",
 "steps": "Sampling steps. More = more detail and slower. With a Lightning / Turbo LoRA use 4-8; without, 20-40.",
 "cfg": "How strictly the model follows the prompt (and the negative). 1 with Lightning LoRAs; 2.5-4 for Qwen-Image; 5-7 for SDXL. Too high = burnt, oversaturated.",
 "sampler_name": "Sampling algorithm. euler is a safe default.",
 "scheduler": "Noise schedule. simple / normal / karras; keep the workflow default unless you know why.",
 "shift": "Model sampling shift (Qwen / Flux). Higher = more global structure, lower = more fine detail. 3 is the usual value.",
 ("ControlNetApplyAdvanced", "strength"): "How strongly the control image (depth, edges...) is followed. 0.6-0.8 for depth; too high copies the CG look of the render.",
 ("ControlNetApplyAdvanced", "start_percent"): "When the ControlNet starts acting (0 = from the first step).",
 ("ControlNetApplyAdvanced", "end_percent"): "When the ControlNet stops acting. 1 = until the end. 0.6-0.8 frees the last steps, where fine texture and detail are painted.",
 "strength_model": "Strength of the LoRA. 1 = as trained; lower it to soften its effect.",
 "unet_name": "Diffusion model file (models/diffusion_models). The list comes from your ComfyUI.",
 "ckpt_name": "Checkpoint file (models/checkpoints). The list comes from your ComfyUI.",
 "lora_name": "LoRA file (models/loras). The list comes from your ComfyUI.",
 "control_net_name": "ControlNet file (models/controlnet). The list comes from your ComfyUI.",
 "clip_name": "Text encoder file (models/text_encoders). The list comes from your ComfyUI.",
 "vae_name": "VAE file (models/vae). The list comes from your ComfyUI.",
 ("ComfyBridgeLoadEXR", "mode"): "How the float EXR is turned into an image. Blender, Arnold and most renderers: depth: distance -> 1/z. Nuke ScanlineRender depth: depth: Nuke 1/z. Colour passes: color (linear -> sRGB).",
 ("ComfyBridgeLoadEXR", "channels"): "auto: RGB if the layer has it, otherwise its first channel as grey.",
 "padding": "Context around the mask, in plate pixels. More context = texture scale and light match the plate better.",
 "target_size": "Long edge the masked crop is processed at, in pixels (around 1024 for most models).",
 "max_scale": "How much a small masked area may be enlarged. 1-1.5 keeps the plate scale; higher = more detail but objects may come out too big.",
 "multiple": "Crop sides are rounded to multiples of this.",
 "match_color": "Match level and colour of the generated area to the untouched plate around the mask.",
 "match_strength": "How much of that colour match to apply (0-1).",
 ("ComfyBridgeMarkMask", "strength"): "How strongly the masked area is tinted for the model (0 = no hint). Lower it if the tint shows in the result.",
 "expand": "Grow (or shrink, negative) the mask, in pixels.",
 "low_threshold": "Canny: lower edge threshold. Lower = more edges.",
 "high_threshold": "Canny: upper edge threshold. Lower = more edges.",
 "confidence_threshold": "SAM3: minimum confidence for the matte. Lower it if the object is missed, raise it if other things get matted.",
 ("SAM3Segment", "prompt"): "What to matte in the result for the alpha: bicycle, person, lamp...",
 "transfer": "How the result is written: data as is, or sRGB -> linear for colour.",
 "precision": "half (16-bit float) or float (32-bit).",
}


def default_param_tip(cls, inp, label):
    l = (label or "").strip().lower()
    return (PARAM_TIPS_LABEL.get(l) or PARAM_TIPS.get((cls, inp)) or PARAM_TIPS.get(inp) or "")


TIPS = {
 "backend": "Where the workflow runs: your local (or LAN) ComfyUI at the server address, or Comfy Cloud (paid plan; API key in Documents/ComfyBridge/comfy_cloud_api_key.txt or the COMFY_API_KEY variable). Switching reloads the workflow, because the model lists differ.",
 "server": "Address of your ComfyUI (host:port). It can be another machine on your network. Not used with Comfy Cloud, but kept for when you switch back. Default: env COMFY_BRIDGE_URL or 127.0.0.1:8188.",
 "test": "Check that ComfyUI answers: shows its version, GPU and VRAM. On Comfy Cloud it checks the API key and shows the account.",
 "help": "Quick guide to ComfyBridge.",
 "repo": "Open the project page on GitHub: documentation, updates and issues.",
 "models_info": "Models this workflow needs, checked against your ComfyUI when you load it.",
 "dl_models": "Download the missing models straight into your ComfyUI models folder (asked once, remembered). Large files take a while; a stopped download resumes where it left off.",
 "library": "Workflows in the comfy_bridge_workflows folder. Pick one and press load / reload.",
 "load": "Read the workflow: shows its description, builds the Parameters tab (model lists come from your ComfyUI) and sets up the Inputs tab. Parameters go back to the workflow defaults.",
 "workflow": "API-format .json (in ComfyUI: Workflow > Export (API)). Filled in by the library; to use your own file set library to (choose a workflow).",
 "first": "First frame to send. In frames mode each frame is a separate ComfyUI job; in sequence mode the whole range goes in one job (or in chunks).",
 "mode": "frames: one ComfyUI job per frame (any workflow). sequence: the whole range in one job, as a batch of images. Needed by video / temporal models (SeedVR2, later LTX); faster on Comfy Cloud for depth and upscale. Single-image workflows (edit, inpaint, relight...) only run as frames.",
 "chunk_size": "Sequence mode, batch workflows only: frames per job. 0 = the whole range in one job. Use it to keep each Comfy Cloud job small; a failed chunk can be retried alone. Temporal workflows always send the whole range.",
 "last": "Last frame to send.",
 "in_color": "viewer transform: send what you see in the Viewer (OCIO display/view or Nuke LUT) and undo it on colour results. sRGB: always send sRGB.",
 "send_exposure": "Exposure of the plate before sending, in stops (+1 = twice as bright). Dark renders give the model nothing to work with. Colour results come back with the inverse, so the comp keeps its level.",
 "out_type": "How to read the result. auto: as the workflow says. data: raw values (depth, mattes, normals). color: back to scene linear.",
 "work_size": "Long edge in pixels the plate is sent at (multiples of 32). 0 = original size. Lower = faster and less VRAM; the result is scaled back with match input format.",
 "out_dir": "Where results are written. Empty: <script folder>/comfy_bridge/<node name>.",
 "versioning": "Write each run to a new v001, v002... folder. Nothing is overwritten, so you can compare settings.",
 "match_format": "Scale the result back to the plate format.",
 "mask_source": "Where the mask comes from: the alpha of the mask input or of the plate. Only used by workflows that take a mask.",
 "mask_channels": "alpha: the alpha goes to every channel (normal masks). rgba (as is): colour-coded masks, e.g. red = regenerate, green = take another pass.",
 "mask_grow": "Grow the mask before sending, in pixels. Gives the model room to blend.",
 "mask_soften": "Blur of the sent mask, in pixels.",
 "comp_over_plate": "Output = result keymixed over the plate with the mask: pixels outside the mask stay untouched. Off: the raw result.",
 "comp_feather": "Feather of the mask used for that comp, in pixels.",
 "run": "Render the inputs, upload them, run the workflow and bring the result back as EXR. Nuke waits until it finishes; the progress bar can cancel.",
 "auto_read": "After each run, place a standalone Read next to this node (labelled with its version; colour, exposure and format already undone).",
 "show_result": "Output the latest result from this node instead of the plate.",
 "bake": "Create a standalone Read with the current result now.",
}
_SLOT_TIPS = {
 "from": "Stream this input is taken from: the plate, the aux input or the mask input.",
 "layer": "Layer of that stream: rgba, depth, a Blender or Arnold AOV... If the workflow asks for depth and the plate has ViewLayer_Depth, it is picked automatically.",
 "send": "color (like plate): same viewer transform and send exposure as the plate. data (raw): values as they are. Inputs read with Load EXR always go as raw float EXR.",
}
for _n in range(1, 4):  # one entry per input slot
    for _k, _t in _SLOT_TIPS.items():
        TIPS["x%d_%s" % (_n, _k)] = _t


QUICK_HELP = """ComfyBridge v%s - quick guide

1. Plug the plate (input 1). Optional: mask input, aux input
   (a second image: style reference, another pass...).
2. library -> pick a workflow -> load / reload.
3. Parameters tab: prompt, denoise, models (lists from your ComfyUI).
4. Inputs tab (if the workflow needs extra images): stream + layer
   for each one (e.g. depth <- plate / ViewLayer_Depth).
5. Send to ComfyUI. A Read with the result (v001, v002...) appears
   next to the node.

Tips
- Dark render? Raise send exposure: the result comes back at the
  original level.
- working size 1024 to explore fast, 0 for the final.
- Missing models? 'download missing models' fetches them for you.
- No GPU for it? Set 'run on' to Comfy Cloud (paid plan + API
  key file, see its tooltip). Workflows
  that use ComfyBridge nodes other than Save EXR stay local.
- Hover any knob for its help.

ComfyBridge by David Borrull - MIT licence
github.com/dborrull/nuke-comfy-pipe""" % __version__
