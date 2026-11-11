// ComfyBridge (nuke-comfybridge) - by David Borrull
// https://github.com/dborrull/nuke-comfybridge
// MIT License - Copyright (c) 2026 David Borrull. See LICENSE.
//
// "Export to ComfyBridge": save the current graph as a ComfyBridge library workflow
// (API JSON + _comfy_bridge block) without writing the metadata by hand.
//   - Right-click a node > ComfyBridge: role of a LoadImage (plate / mask / extra input),
//     result node, and which widgets show up as knobs in Nuke.
//   - Menu ComfyBridge > Export to ComfyBridge... (also on the canvas right-click):
//     workflow settings (built from meta_schema.json), labels / ranges, check, save.
// Roles and settings are kept in the UI workflow (node.properties.comfybridge and
// graph.extra.comfybridge), so saving the UI workflow keeps them for the next export.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const PROP = "comfybridge";
const LOADERS = ["LoadImage", "ComfyBridgeLoadEXR"];
const SKIP_WIDGETS = ["control_after_generate", "upload", "image_upload"];
// keys set from the node menu, not from the settings form
const GRAPH_KEYS_FROM_NODES = ["plate_node", "mask_node", "save_node", "inputs", "params"];

let schemaCache = null;
async function schema() {
    if (!schemaCache) {
        const r = await api.fetchApi("/comfybridge/schema");
        if (!r.ok) throw new Error("ComfyBridge routes not found: restart ComfyUI after installing.");
        schemaCache = await r.json();
    }
    return schemaCache;
}

// ---------- per-node / per-graph state ----------

function nodeState(node) {
    node.properties = node.properties || {};
    if (!node.properties[PROP]) node.properties[PROP] = {};
    const s = node.properties[PROP];
    s.params = s.params || {};
    return s;
}

function graphState() {
    const g = app.graph;
    g.extra = g.extra || {};
    if (!g.extra[PROP]) g.extra[PROP] = {};
    return g.extra[PROP];
}

function markDirty() {
    app.graph.setDirtyCanvas(true, true);
    try { app.graph.change(); } catch (e) { /* older frontends */ }
}

function nodes() {
    return app.graph._nodes || app.graph.nodes || [];
}

function setRole(node, role) {
    const cls = node.comfyClass || node.type;
    // one plate, one mask, one result node
    if (role === "plate" || role === "mask" || role === "save") {
        for (const n of nodes()) {
            const s = n.properties?.[PROP];
            if (n !== node && s?.role === role) delete s.role;
        }
    }
    const s = nodeState(node);
    if (!role || s.role === role) delete s.role;
    else s.role = role;
    if (s.role === "input" && !s.input) {
        s.input = { label: node.title || cls, from: "aux", send: "color" };
    }
    markDirty();
}

function widgetSpec(node, name) {
    const d = node.constructor?.nodeData?.input || {};
    return (d.required || {})[name] || (d.optional || {})[name] || null;
}

function guessType(node, w) {
    const spec = widgetSpec(node, w.name);
    const t = spec && spec[0];
    if (t === "INT") return "int";
    if (t === "FLOAT") return "float";
    if (t === "BOOLEAN") return "bool";
    if (t === "STRING") return spec[1]?.multiline ? "text" : "string";
    if (typeof w.value === "boolean") return "bool";
    if (typeof w.value === "number") return Number.isInteger(w.value) ? "int" : "float";
    return "string";
}

function exposable(node) {
    return (node.widgets || []).filter(w => w.name && !SKIP_WIDGETS.includes(w.name)
        && !(LOADERS.includes(node.comfyClass) && w.name === "image"));
}

function toggleParam(node, w) {
    const s = nodeState(node);
    if (s.params[w.name]) delete s.params[w.name];
    else s.params[w.name] = { label: w.name, type: guessType(node, w), _order: Date.now() };
    markDirty();
}

// ---------- node menu ----------

function nodeMenu(node) {
    const cls = node.comfyClass || node.type;
    const s = node.properties?.[PROP] || {};
    const tick = on => (on ? "✓ " : "  ");
    const opts = [];
    if (LOADERS.includes(cls)) {
        if (cls === "LoadImage") {
            opts.push({ content: tick(s.role === "plate") + "Plate", callback: () => setRole(node, "plate") });
            opts.push({ content: tick(s.role === "mask") + "Mask", callback: () => setRole(node, "mask") });
        }
        opts.push({ content: tick(s.role === "input") + "Extra input (depth, reference...)",
                    callback: () => setRole(node, "input") });
    }
    if (/Save|Preview/.test(cls)) {
        opts.push({ content: tick(s.role === "save") + "Result from this node", callback: () => setRole(node, "save") });
    }
    const ws = exposable(node);
    if (ws.length) {
        if (opts.length) opts.push(null);
        for (const w of ws) {
            opts.push({ content: tick(!!s.params?.[w.name]) + "Knob in Nuke: " + w.name,
                        callback: () => toggleParam(node, w) });
        }
    }
    opts.push(null, { content: "Export to ComfyBridge...", callback: () => openDialog() });
    return [{
        content: "ComfyBridge", has_submenu: true,
        callback: (value, options, e, menu) => {
            new window.LiteGraph.ContextMenu(opts, { event: e, parentMenu: menu, node });
            return true;
        },
    }];
}

// ---------- build the API workflow ----------

async function buildWorkflow(settings) {
    const { output } = await app.graphToPrompt();
    const meta = {};
    for (const [k, v] of Object.entries(settings)) {
        if (k !== "file" && v !== "" && v !== undefined && v !== null) meta[k] = v;
    }
    const skipped = [];
    const inputs = [], params = [];
    for (const n of nodes()) {
        const s = n.properties?.[PROP];
        if (!s) continue;
        const id = String(n.id);
        if (!(id in output)) {
            if (s.role || Object.keys(s.params || {}).length) skipped.push(`${n.title} (#${id}) is muted or bypassed`);
            continue;
        }
        if (s.role === "plate") meta.plate_node = id;
        if (s.role === "mask") meta.mask_node = id;
        if (s.role === "save") meta.save_node = id;
        if (s.role === "input") inputs.push(Object.assign({ node: id }, s.input || {}));
        for (const [inp, p] of Object.entries(s.params || {})) {
            const val = output[id].inputs?.[inp];
            if (val === undefined) { skipped.push(`${n.title}.${inp}: not an input of the API workflow`); continue; }
            if (Array.isArray(val)) { skipped.push(`${n.title}.${inp}: fed by a link`); continue; }
            const q = { label: p.label || inp, node: id, input: inp };
            for (const [k, v] of Object.entries(p)) {
                if (k !== "label" && !k.startsWith("_") && v !== "" && v !== undefined && v !== null) q[k] = v;
            }
            params.push([p._order || 0, q]);
        }
    }
    if (inputs.length) meta.inputs = inputs;
    if (params.length) meta.params = params.sort((a, b) => a[0] - b[0]).map(x => x[1]);
    return { workflow: Object.assign({}, output, { _comfy_bridge: meta }), skipped };
}

// ---------- load a library workflow back ----------

async function loadFromLibrary(file) {
    const r = await api.fetchApi("/comfybridge/library/" + encodeURIComponent(file));
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.statusText);
    const meta = data._comfy_bridge || {};
    const apiNodes = {};
    for (const [k, v] of Object.entries(data)) if (!k.startsWith("_")) apiNodes[k] = v;
    const before = app.graph;
    await app.loadApiJson(apiNodes, file);
    // the frontend builds the new graph a moment later: wait for its nodes
    const want = Object.keys(apiNodes);
    let byId = {};
    for (let i = 0; i < 50; i++) {
        byId = {};
        for (const n of nodes()) byId[String(n.id)] = n;
        if ((app.graph !== before || i > 0) && want.every(id => byId[id])) break;
        await new Promise(r => setTimeout(r, 100));
    }
    const g = graphState();
    for (const k of Object.keys(g)) delete g[k];
    for (const [k, v] of Object.entries(meta)) if (!GRAPH_KEYS_FROM_NODES.includes(k)) g[k] = v;
    g.file = file;
    const lost = [];
    const role = (id, r2, extra) => {
        const n = byId[String(id)];
        if (!n) { lost.push(`${r2}: node ${id}`); return; }
        const s = nodeState(n);
        s.role = r2;
        if (extra) s.input = extra;
    };
    if (meta.plate_node) role(meta.plate_node, "plate");
    if (meta.mask_node) role(meta.mask_node, "mask");
    if (meta.save_node) role(meta.save_node, "save");
    for (const d of meta.inputs || []) {
        const { node, ...rest } = d;
        role(node, "input", rest);
    }
    (meta.params || []).forEach((p, i) => {
        const n = byId[String(p.node)];
        if (!n) { lost.push(`param ${p.label || p.input}: node ${p.node}`); return; }
        const { node, input, ...rest } = p;
        nodeState(n).params[input] = Object.assign(rest, { _order: i + 1 });
    });
    markDirty();
    return lost;
}

// ---------- dialog ----------

const CSS = `
.cbx-back{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:10000;display:flex;align-items:center;justify-content:center}
.cbx{background:var(--comfy-menu-bg,#222);color:var(--fg-color,#ddd);border:1px solid var(--border-color,#444);border-radius:8px;
 width:min(860px,94vw);max-height:90vh;overflow:auto;padding:14px 18px;font:13px sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.5)}
.cbx h2{margin:0 0 8px;font-size:16px}.cbx h3{margin:14px 0 6px;font-size:13px;opacity:.85}
.cbx label{display:block;margin:4px 0}.cbx .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.cbx input,.cbx select,.cbx textarea{background:var(--comfy-input-bg,#111);color:inherit;border:1px solid var(--border-color,#444);
 border-radius:4px;padding:3px 6px;font:inherit}
.cbx textarea{width:100%;min-height:44px}.cbx .k{display:inline-block;min-width:130px;opacity:.8}
.cbx table{border-collapse:collapse;width:100%}.cbx td,.cbx th{padding:2px 4px;text-align:left;vertical-align:top}
.cbx th{font-weight:normal;opacity:.7}.cbx td input{width:100%;box-sizing:border-box}
.cbx .muted{opacity:.65}.cbx .warn{color:#f0b050}.cbx .ok{color:#7c7}.cbx .err{color:#f66}
.cbx .btns{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}
.cbx button{background:var(--comfy-input-bg,#333);color:inherit;border:1px solid var(--border-color,#555);border-radius:4px;padding:5px 12px;cursor:pointer}
.cbx button.primary{background:#2f6fbf;border-color:#2f6fbf;color:#fff}
.cbx details{margin-top:6px}.cbx summary{cursor:pointer;opacity:.85}`;

function el(tag, attrs = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === "class") e.className = v;
        else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
        else if (v !== undefined && v !== null) e.setAttribute(k, v);
    }
    for (const k of kids) if (k !== null && k !== undefined) e.append(k);
    return e;
}

function field(key, spec, value, onChange) {
    // a form field for one schema key; "" = not set (ComfyBridge default)
    const def = spec.default !== undefined ? ` (default: ${spec.default})` : "";
    let input;
    if (spec.type === "enum" || spec.type === "bool") {
        const values = spec.type === "bool" ? ["true", "false"] : spec.values;
        input = el("select", {}, el("option", { value: "" }, "—" + def));
        for (const v of values) input.append(el("option", { value: v }, v));
        input.value = value === undefined ? "" : String(value);
        input.addEventListener("change", () => {
            const v = input.value;
            onChange(v === "" ? undefined : spec.type === "bool" ? v === "true" : v);
        });
    } else if (spec.type === "text") {
        input = el("textarea", { placeholder: def.trim() });
        input.value = value ?? "";
        input.addEventListener("input", () => onChange(input.value || undefined));
    } else {
        const num = spec.type === "int" || spec.type === "number";
        input = el("input", { type: num ? "number" : "text", placeholder: def.trim(),
                              step: spec.type === "int" ? "1" : "any" });
        input.value = value ?? "";
        input.addEventListener("input", () => {
            const v = input.value;
            onChange(v === "" ? undefined : num ? Number(v) : v);
        });
    }
    return el("label", { title: spec.doc || "" }, el("span", { class: "k" }, key), input);
}

let dialogOpen = false;

async function openDialog() {
    if (dialogOpen) return;
    let sch;
    try { sch = await schema(); } catch (e) { alert(e.message); return; }
    dialogOpen = true;
    if (!document.getElementById("cbx-css")) document.head.append(el("style", { id: "cbx-css" }, CSS));
    let g = graphState();
    const keys = sch.keys;
    const back = el("div", { class: "cbx-back" });
    const box = el("div", { class: "cbx" });
    back.append(box);
    const close = () => { back.remove(); dialogOpen = false; document.removeEventListener("keydown", onKey); };
    const onKey = e => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    back.addEventListener("mousedown", e => { if (e.target === back) close(); });

    const result = el("div");
    const render = () => {
        box.replaceChildren();
        box.append(el("h2", {}, "Export to ComfyBridge"));

        // library: open one to edit
        const libSel = el("select", {}, el("option", { value: "" }, "open a library workflow to edit..."));
        // on / off in Nuke's library pulldown (the files are not touched)
        const onOff = el("details", {}, el("summary", {}, "Workflows in Nuke (on / off)"));
        api.fetchApi("/comfybridge/library").then(r => r.json()).then(d => {
            libSel.title = d.folder;
            for (const w of d.workflows || []) {
                libSel.append(el("option", { value: w.file }, `${w.name}  (${w.file})` + (w.enabled === false ? "  [off]" : "")));
                const cb = el("input", { type: "checkbox" });
                cb.checked = w.enabled !== false;
                const msg = el("span", { class: "muted" });
                cb.addEventListener("change", async () => {
                    try {
                        const r = await api.fetchApi("/comfybridge/enabled/" + encodeURIComponent(w.file), {
                            method: "POST", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ enabled: cb.checked }) });
                        const res = await r.json();
                        if (!r.ok) throw new Error(res.error || r.statusText);
                        msg.textContent = "  saved: load / reload in Nuke";
                    } catch (e) { cb.checked = !cb.checked; msg.textContent = "  " + e.message; }
                });
                onOff.append(el("label", { title: w.file }, cb, " " + w.name, msg));
            }
        }).catch(() => {});
        libSel.addEventListener("change", async () => {
            if (!libSel.value) return;
            try {
                const lost = await loadFromLibrary(libSel.value);
                g = graphState();   // a new graph
                render();
                if (lost.length) result.replaceChildren(el("div", { class: "warn" }, "Not found in the graph: " + lost.join(", ")));
            } catch (e) { result.replaceChildren(el("div", { class: "err" }, e.message)); }
        });
        box.append(el("div", { class: "row" }, libSel), onOff);

        // file + main keys
        const file = el("input", { type: "text", placeholder: "my_workflow.json", style: "width:260px" });
        file.value = g.file || "";
        file.addEventListener("input", () => { g.file = file.value.trim(); markDirty(); });
        box.append(el("label", {}, el("span", { class: "k" }, "file"), file,
                      el("span", { class: "muted" }, "  in the ComfyBridge library")));
        const set = k => v => { if (v === undefined) delete g[k]; else g[k] = v; markDirty(); };
        const main = ["name", "description", "output", "sequence"];
        for (const k of main) box.append(field(k, keys[k], g[k], set(k)));

        // roles (from the node menu)
        box.append(el("h3", {}, "Images  (right-click a node > ComfyBridge)"));
        const roles = el("table");
        const all = nodes();
        const who = r => all.filter(n => n.properties?.[PROP]?.role === r);
        const name = n => `${n.title} #${n.id}`;
        const plate = who("plate")[0], mask = who("mask")[0], save = who("save")[0];
        roles.append(el("tr", {}, el("th", {}, "plate"), el("td", {}, plate ? name(plate)
            : el("span", { class: "muted" }, "first LoadImage (not set)"))));
        roles.append(el("tr", {}, el("th", {}, "mask"), el("td", {}, mask ? name(mask) : el("span", { class: "muted" }, "none"))));
        roles.append(el("tr", {}, el("th", {}, "result"), el("td", {}, save ? name(save)
            : el("span", { class: "muted" }, "first save / preview node (not set)"))));
        const ikeys = keys.inputs.item.keys;
        for (const n of who("input")) {
            const s = nodeState(n);
            s.input = s.input || {};
            const cell = el("div", { class: "row" });
            for (const k of ["label", "from", "layer", "send", "mono", "match_exposure"]) {
                cell.append(field(k, ikeys[k], s.input[k], v => {
                    if (v === undefined) delete s.input[k]; else s.input[k] = v; markDirty();
                }));
            }
            roles.append(el("tr", {}, el("th", {}, "input"), el("td", {}, name(n), cell)));
        }
        box.append(roles);

        // params
        box.append(el("h3", {}, "Knobs in Nuke  (right-click a node > ComfyBridge > Knob in Nuke)"));
        const rows = [];
        for (const n of all) {
            for (const [inp, p] of Object.entries(n.properties?.[PROP]?.params || {})) rows.push([p._order || 0, n, inp, p]);
        }
        rows.sort((a, b) => a[0] - b[0]);
        if (!rows.length) {
            box.append(el("div", { class: "muted" }, "None: only the KSampler settings will show up in Nuke."));
        } else {
            const t = el("table", {}, el("tr", {}, ...["node.input", "label", "type", "min", "max", "tooltip", ""]
                .map(h => el("th", {}, h))));
            const pkeys = keys.params.item.keys;
            for (const [, n, inp, p] of rows) {
                const cellIn = (k, attrs = {}) => {
                    const i = el("input", Object.assign({ type: "text" }, attrs));
                    i.value = p[k] ?? "";
                    i.addEventListener("input", () => {
                        const v = i.value;
                        if (v === "") delete p[k];
                        else p[k] = attrs.type === "number" ? Number(v) : v;
                        markDirty();
                    });
                    return el("td", {}, i);
                };
                const ty = el("select", {}, ...pkeys.type.values.map(v => el("option", { value: v }, v)));
                ty.value = p.type || "string";
                ty.addEventListener("change", () => { p.type = ty.value; markDirty(); });
                const rm = el("button", { title: "remove", onclick: () => {
                    delete n.properties[PROP].params[inp]; markDirty(); render(); } }, "×");
                t.append(el("tr", {}, el("td", { class: "muted" }, `${n.title} #${n.id}.${inp}`),
                    cellIn("label"), el("td", {}, ty), cellIn("min", { type: "number", step: "any", style: "width:70px" }),
                    cellIn("max", { type: "number", step: "any", style: "width:70px" }), cellIn("tooltip"), el("td", {}, rm)));
            }
            box.append(t);
        }

        // everything else in the schema, folded
        const more = el("details", {}, el("summary", {}, "More settings"));
        for (const [k, spec] of Object.entries(keys)) {
            if (main.includes(k) || GRAPH_KEYS_FROM_NODES.includes(k) || spec.type === "list") continue;
            if (spec.type === "object") {
                g[k] = g[k] || {};
                for (const [sk, sspec] of Object.entries(spec.keys)) {
                    more.append(field(`${k}.${sk}`, sspec, g[k][sk], v => {
                        g[k] = g[k] || {};
                        if (v === undefined) delete g[k][sk]; else g[k][sk] = v;
                        if (!Object.keys(g[k]).length) delete g[k];
                        markDirty();
                    }));
                }
                if (!Object.keys(g[k]).length) delete g[k];
                continue;
            }
            more.append(field(k, spec, g[k], set(k)));
        }
        box.append(more);

        box.append(result);
        const check = el("button", { onclick: () => send(false) }, "Check");
        const save2 = el("button", { class: "primary", onclick: () => send(true) }, "Save to library");
        box.append(el("div", { class: "btns" }, el("button", { onclick: close }, "Close"), check, save2));
    };

    const show = (lines, cls) => el("ul", {}, ...lines.map(l => el("li", { class: cls }, l)));

    async function send(save) {
        result.replaceChildren(el("div", { class: "muted" }, save ? "Saving..." : "Checking..."));
        try {
            const settings = Object.assign({}, g);
            const { workflow, skipped } = await buildWorkflow(settings);
            const file = (g.file || "").trim();
            let url = "/comfybridge/check", body = { workflow };
            if (save) {
                if (!file) throw new Error("Give it a file name.");
                url = "/comfybridge/library/" + encodeURIComponent(file.endsWith(".json") ? file : file + ".json");
            }
            let r = await api.fetchApi(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                             body: JSON.stringify(body) });
            let d = await r.json();
            if (save && r.status === 409 && confirm(d.error + "\nOverwrite it? (the old one is kept as .bak)")) {
                body = { workflow, overwrite: true };
                r = await api.fetchApi(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify(body) });
                d = await r.json();
            }
            if (!r.ok) throw new Error(d.error || r.statusText);
            const out = [];
            if (save) out.push(el("div", { class: "ok" }, `Saved ${d.path}` + (d.backup ? `  (old one: ${d.backup})` : "")
                + ". In Nuke: load / reload."));
            else out.push(el("div", { class: (d.warnings.length || skipped.length) ? "warn" : "ok" },
                (d.warnings.length || skipped.length) ? "Problems:" : "No problems."));
            if (skipped.length) out.push(show(skipped.map(s => "left out: " + s), "warn"));
            if (d.warnings.length) out.push(show(d.warnings, "warn"));
            if (d.cloud.length) out.push(el("div", { class: "muted" }, "Not on Comfy Cloud (local only): " + d.cloud.join(", ")));
            result.replaceChildren(...out);
        } catch (e) {
            result.replaceChildren(el("div", { class: "err" }, e.message));
        }
    }

    render();
    document.body.append(back);
}

app.registerExtension({
    name: "ComfyBridge.Export",
    commands: [{ id: "ComfyBridge.Export", label: "Export to ComfyBridge...", function: () => openDialog() }],
    menuCommands: [{ path: ["ComfyBridge"], commands: ["ComfyBridge.Export"] }],
    getNodeMenuItems(node) {
        return [null, ...nodeMenu(node)];
    },
    getCanvasMenuItems() {
        return [null, { content: "Export to ComfyBridge...", callback: () => openDialog() }];
    },
});
