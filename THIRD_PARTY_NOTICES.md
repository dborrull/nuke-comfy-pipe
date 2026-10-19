# Third-party notices

nuke-comfy-pipe is MIT licensed (see LICENSE). It builds on the work below.
The **models** these workflows load are not covered by any of these licences:
each model has its own (see "Model licences" in README.md).

## ComfyUI workflow templates (Comfy-Org)

Adapted for ComfyBridge (inputs, output node and parameter metadata changed):

| Workflow in this repo | Original template |
|---|---|
| `examples/workflows/upscale_seedvr2_3b_int8.json` | `utility_seedvr2_3b_int8_upscale_image` |
| `examples/workflows/edit_qwen_image_2_1.json` | `image_qwen_image_2_1_image_edit` |

Source: https://github.com/Comfy-Org/workflow_templates
(distributed as the `comfyui-workflow-templates` package, licence: MIT)

```
MIT License

Copyright (c) 2023-present Comfy Org

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Ideas and approaches

- **nuke-comfyui-bridge** by Mario Falco (MIT): display/view colour round-trip and
  workflow metadata ideas. https://github.com/Mariolepipe/nuke-comfyui-bridge
- **comfyui-exr-plate** by Samuelsujan (MIT): round-trip test and auto-versioning
  ideas. https://github.com/Samuelsujan/comfyui-exr-plate

No code was copied from these two projects.
