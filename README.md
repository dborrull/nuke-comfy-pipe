# nuke-comfy-pipe

**ComfyBridge** — run ComfyUI workflows on your plates from inside Nuke and get the result back as EXR, with the colour pipeline handled.

Send a plate → pick a workflow (depth, realism pass, your own…) → tweak its parameters in Nuke → the result comes out of the node. No exporting, no browser, no 8-bit PNGs in your comp.

> Status: **v0.8.0 — early, but working.** Tested on Windows, Nuke 17.1 (Non-commercial) and ComfyUI 0.38 portable with an 8 GB GPU. The Comfy Cloud backend is new and has had little real-world use yet.

## Features

- **Workflow library with parameters.** Each workflow can carry a small metadata block; its parameters (denoise, prompt, seed, model…) show up as knobs in a *Parameters* tab.
- **Colour round-trip.** The plate is sent through the active Viewer transform (OCIO display/view, or Nuke's own LUTs) and colour results come back through the inverse, so they line up with the original. A round-trip test workflow is included in `examples/`.
- **Multi-input (3D → AI).** A workflow can ask for up to 3 extra images — depth, a second pass, AOVs — taken from a **layer of the plate** (multichannel EXR from Blender, Maya…), from the **aux input** or from the **mask input**. Data goes as float EXR and is normalised in ComfyUI (depth from Blender distance or Nuke 1/z → near = white for depth ControlNets); colour goes through the same viewer transform as the plate.
- **Model dropdowns.** Model / LoRA / ControlNet parameters list what your ComfyUI actually has (read from `/object_info`); a file in another subfolder is found automatically, and missing ones are reported on load.
- **Send exposure.** Brighten (or darken) what is sent, in stops — dark renders give the model nothing to work with. Colour results come back with the inverse, so the comp stays put.
- **Masked inpainting and editing.** Connect a mask (or use the plate alpha); the masked area is cropped with context, processed at the model's resolution, stitched back and comped over the plate with a feathered mask — pixels outside the mask stay untouched.
- **Float EXR output.** A self-contained ComfyUI node (*Save EXR (ComfyBridge)*) writes float EXR with no extra dependencies. Data passes (depth) come back raw; images come back to linear.
- **Versioned outputs.** Every run goes to a new `v001`, `v002`… folder — nothing is overwritten, so you can compare settings.
- **Working size.** Optionally downscale before sending (multiples of 32) and scale back on return. Handy with limited VRAM.
- **Clean node graph.** After each run a standalone Read (labelled with its version, plus inverse colour transform and Reformat if needed) is placed next to the node. Or turn that off and the result comes out of the ComfyBridge node itself.
- **Local ComfyUI or Comfy Cloud.** A *run on* switch sends the work to your own ComfyUI (or one on your network) or to Comfy Cloud, for models that don't fit your GPU. See [Comfy Cloud](#comfy-cloud).
- **Works within Nuke Non-commercial's Python limit** (10 Node objects per session): the node is pasted from a template and its internals are driven by expressions, so a run touches one node.

## Models

You don't need to hunt for model files. When you load a workflow, the node shows which models it needs and whether your ComfyUI has them. Press **download missing models** and they go straight into your ComfyUI models folder (you're asked for that folder once). Downloads can be cancelled and resume where they stopped. A few models have no direct link (e.g. Civitai checkpoints): the node tells you where to get them.

The list of known models lives in `nuke/comfybridge/models_catalog.json`; add your own entries there or in a workflow's metadata (`"models": [{"file": ..., "folder": ..., "url": ...}]`).

## Install

**ComfyUI**
1. Copy `comfyui/ComfyUI-ComfyBridge` to `ComfyUI/custom_nodes/`.
2. Restart ComfyUI.

**Nuke**
1. Copy `nuke/comfy_bridge.py`, the `nuke/comfybridge` folder and the `nuke/comfy_bridge_workflows` folder to your `~/.nuke`.
2. Add to `~/.nuke/menu.py`:
   ```python
   import comfy_bridge
   comfy_bridge.install_menu()
   ```
3. Restart Nuke → **Nodes › ComfyUI › ComfyBridge**.

Set `COMFY_BRIDGE_URL` to point at a ComfyUI on another machine, and `COMFY_BRIDGE_WORKFLOWS` to use a shared workflow folder.

## Usage

1. Select your plate and create a **ComfyBridge**.
2. **test connection**.
3. Pick a workflow in **library** → **load / reload**. Adjust it in the **Parameters** tab.
4. Set the frame range → **Send to ComfyUI**.
5. A Read with the result (labelled v001, v002…) appears next to the node. With *create Read after each run* off, the node outputs the result itself (*show result in this node*).

Hover any knob for its help, or press **help** next to *test connection* for a quick guide.

| Knob | What it does |
|---|---|
| run on | *local ComfyUI* (the *server* address) or *Comfy Cloud*. |
| send exposure (stops) | Exposure applied before sending (plate and inputs marked `match_exposure`); undone on colour results. |
| input color | *viewer transform*: send what you see in the Viewer, undo it on return. *sRGB*: always sRGB. |
| result | *auto* uses the workflow's metadata; force *data* (raw) or *color* (back to linear). |
| working size | Long edge in px (0 = original). |
| new version per run | Write each run to a new `vNNN` folder. |
| match input format | Scale the result back to the plate's format. |
| mask from | *mask input (alpha)* or *plate alpha*. Only used by workflows that take a mask. |
| grow / soften | Expand and soften the mask sent to ComfyUI (gives the model room to blend). |
| comp result over plate with mask | Output = result keymixed over the plate. Turn off to see the raw result. |
| comp feather | Feather of the comp mask. |
| mask send | *alpha*, or *rgba (as is)* for colour-coded masks (e.g. red = regenerate, green = take another pass). |

### Inputs tab (v0.6)

When a workflow needs extra images, they show up here. For each one:

| Knob | What it does |
|---|---|
| from | *plate*, *aux input* or *mask input* — the stream to take it from. |
| layer | Which layer of that stream (`rgba`, `depth`, `P`, your custom passes…). Bring a pass into the plate with a Copy/ShuffleCopy, or plug it into **aux**. |
| send | *color (like plate)* or *data (raw)*. Inputs that the workflow reads with **Load EXR (ComfyBridge)** always go as raw float EXR. |

Every extra input is resized to the plate (or the working size).

**First thing to try:** *Test – colour round-trip* (in [`examples/`](examples/README.md)). `Merge (difference)` the result against the original and gain up: it should be ~black (8-bit quantisation and highlights beyond the display range aside).

## Included workflows

| Workflow | Output | Needs | Comfy Cloud |
|---|---|---|---|
| Depth – Depth Anything V2 | float EXR, raw | [ComfyUI-DepthAnythingV2](https://github.com/kijai/ComfyUI-DepthAnythingV2) | yes |
| Realism – SDXL img2img + Tile | half EXR, linear | an SDXL checkpoint (e.g. epiCRealism XL) + SDXL Tile ControlNet | yes (pick a checkpoint Cloud has) |
| Inpaint – SDXL (mask) | half EXR, comped over plate | an SDXL checkpoint | no (crop/stitch nodes) |
| Edit – Qwen-Image-Edit 2509 (mask) | half EXR, comped over plate | Qwen-Image-Edit 2509 (fp8), Qwen 2.5 VL text encoder, Qwen-Image VAE, Lightning 4-step LoRA, SAM3 | no (crop/stitch nodes) |
| Render to photo – Qwen-Image 2512 + depth (3D) | half EXR, linear | Qwen-Image 2512 (fp8), Qwen 2.5 VL text encoder, Qwen-Image VAE, Qwen-Image 2512 Fun ControlNet | no (Load EXR) |
| Render to photo – … FAST (Lightning 4 steps) | half EXR, linear | + Qwen-Image 2512 Lightning 4-step LoRA | no (Load EXR) |
| Render to photo – … depth image (PNG) | half EXR, linear | same as Render to photo; depth as an 8-bit image (e.g. from Depth Anything) | yes |

Tools (colour round-trip test, extra-input test) and untested or experimental workflows (SeedVR2 upscale, Qwen Image 2.1 edit, edges, style reference, mix passes) are in [`examples/`](examples/README.md).

## Comfy Cloud

For models that don't fit your GPU, set **run on** to *Comfy Cloud*. You need a paid Comfy Cloud plan and an API key (platform.comfy.org → API Keys).

- Save the key, on one line, in `Documents/ComfyBridge/comfy_cloud_api_key.txt`, or set the `COMFY_API_KEY` environment variable. It is never written to the Nuke script.
- **test connection** checks the key and shows the account.
- On Cloud, *Save EXR (ComfyBridge)* is replaced on the fly by ComfyUI's own *Save Image (Advanced)* in EXR, so results still come back as float EXR. The other ComfyBridge nodes (Load EXR, Crop To Mask / Stitch) are not on Cloud: those workflows are flagged *not for Cloud* when loaded.
- Model lists in *Parameters* come from Cloud. If a workflow names a model Cloud doesn't have, pick another one from the list.
- Cancelling also removes the frames still queued on Cloud, so they are not charged.

### From 3D: render → photo

1. Render from Blender (or any DCC) a multichannel EXR with `rgba` and the Z pass (`depth`). In Nuke, Read it and plug it into ComfyBridge.
2. Load **Render to photo – Qwen-Image 2512 + depth**. The Inputs tab shows *depth ← plate / depth*.
3. *depth mode*: **distance** for Blender, Arnold and most renderers (Z = distance, sky = far); **Nuke 1/z** for ScanlineRender.
4. Check the depth first with **Test – extra input**: near should be white, the sky black.
5. *denoise* ~0.6 keeps the look of your render; ~0.9 reinvents the textures while the depth keeps the geometry.

**Mix passes + inpaint** then combines two of those results with a colour mask (sent as RGBA): GREEN takes the aux pass, RED is regenerated following the depth — e.g. the walls.

> Qwen-Image 2512 is a 20B model. On 8 GB of VRAM the fp8 file runs with offloading (slow); for production point *server* to a bigger GPU on your network, or run it on Comfy Cloud. The model file names are parameters, so a GGUF or another quantisation is a knob change (with the matching loader).

### Masks

Masked workflows declare `"mask_node"` in their metadata. ComfyBridge renders the mask (grown and softened) as a second image, and the workflow uses **Crop To Mask (ComfyBridge)** → model → **Stitch (ComfyBridge)**: only the masked region plus some context is processed, at around 1024 px on its long edge (never enlarged more than `max_scale`), then pasted back into the full frame. Back in Nuke, the result is keymixed over the plate with the feathered mask.

Prompting tips: for **SDXL inpaint** describe what *should* be there ("old brick wall"), not what to remove. For **Qwen-Image-Edit** write an instruction ("remove the cable and continue the wall").

### Your own workflows

Export from ComfyUI with **Workflow › Export (API)**. It needs a `LoadImage` and a `Save EXR (ComfyBridge)` (or `SaveImage`). Optionally add:

```json
"_comfy_bridge": {
  "name": "My workflow",
  "description": "What it does",
  "output": "data | color",
  "params": [
    {"label": "denoise", "node": "8", "input": "denoise", "type": "float", "min": 0, "max": 1},
    {"label": "prompt +", "node": "3", "input": "text", "type": "text"},
    {"label": "model", "node": "2", "input": "ckpt_name", "options": ["a.safetensors", "b.safetensors"]}
  ]
}
```

Optional keys:

| Key | Meaning |
|---|---|
| `"mask_node": "11"` | LoadImage that receives the mask. |
| `"mask_channels": "rgba"` | Send the mask as RGBA (default: alpha in all channels). |
| `"plate_node": "1"` | LoadImage that receives the plate (default: the first LoadImage that is not the mask or an extra input). |
| `"matte": true` | The result's alpha is a matte: output it as alpha. |
| `"match_exposure": true` (in an input) | That input gets the same send exposure as the plate. |
| `"inputs": [...]` | Extra images, up to 3: `{"node": "2", "label": "depth", "from": "plate", "layer": "depth", "mono": true, "send": "data"}`. `from`: `plate` / `aux` / `mask`. A `LoadImage` receives an 8-bit PNG; a **Load EXR (ComfyBridge)** receives a float EXR. |

Drop it in `comfy_bridge_workflows` and press **load / reload**. More workflows (tools and experiments) are in [`examples/`](examples/README.md).

### ComfyUI nodes

| Node | What it does |
|---|---|
| Save EXR (ComfyBridge) | Float/half EXR output, no dependencies. |
| Load EXR (ComfyBridge) | Reads scanline EXR (none / ZIP compression — Nuke's defaults). Modes: *color (linear → sRGB)*, *data*, *depth: distance → 1/z*, *depth: distance normalize + invert*, *depth: Nuke 1/z*. |
| Crop To Mask / Stitch (ComfyBridge) | Process only the masked region at the model's resolution and paste it back, optional colour match and matte. |
| Mark Mask (ComfyBridge) | Tints the masked area for instruction-edit models. |

## Model licences — read before commercial work

The code is MIT. **The models are not covered by it.** Some common ones are non-commercial:

- Depth Anything V2 **Large** — CC-BY-NC-4.0 (non-commercial). **Small** — Apache 2.0.
- Qwen-Image / Qwen-Image 2512 / Qwen-Image-Edit — Apache 2.0. Check each ControlNet's own licence.
- SeedVR2 (ByteDance) — Apache 2.0.
- FLUX.1 dev / Kontext dev and FLUX.2 [klein] 9B — non-commercial. FLUX.2 [klein] 4B — Apache 2.0.
- SDXL-based checkpoints — check each checkpoint's own licence.

Check every model's licence before using results on paid work.

## Known issues / notes

- **Troubleshooting from real use:** a broken `xformers` install can make Depth Anything output smooth blobs (uninstall it); some versions of the DepthAnythingV2 node fail with *"more than one element of the written-to tensor refers to a single memory location"* on sizes that are exact multiples of 14 (replace `.expand(-1, -1, -1, 3)` with `.repeat(1, 1, 1, 3)` in its `nodes.py`).
- Frames are processed one by one (one job per frame). A sequence mode for video models is on the roadmap. Image models (SDXL, Qwen) are not temporally stable: expect flicker on sequences.
- Nuke blocks while ComfyUI works (the progress bar can cancel).
- Nuke Non-commercial caps output at HD and saves `.nknc` scripts.

## Roadmap

- OCIO input transform per input (colourspace, display/view, look, CDL/LUT file) and an ACEScct option.
- LTX-2.5 on Comfy Cloud: alpha generation (matte without a green screen), SDR → HDR, restore, with tiled sampling for long and high-resolution shots.
- Matte refinement (SDMatte / BiRefNet).
- Video depth with temporal consistency (DepthCrafter), batches with overlap.
- Sequence mode (one job for the whole frame range) for video models: object removal in video, LTX / Wan.
- Background execution.

## Credits

Full notices, including adapted ComfyUI templates: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

- Display/view colour round-trip and workflow metadata ideas: [nuke-comfyui-bridge](https://github.com/Mariolepipe/nuke-comfyui-bridge) by Mario Falco (MIT).
- Round-trip test and auto-versioning ideas: [comfyui-exr-plate](https://github.com/Samuelsujan/comfyui-exr-plate) (MIT).

## How it was made

Designed and tested in production by David Borrull, a VFX compositor. The code was written with Claude (Anthropic), directed and tested shot by shot in Nuke 17 and ComfyUI. Version history: [CHANGELOG.md](CHANGELOG.md).

## Licence

MIT © 2026 David Borrull
