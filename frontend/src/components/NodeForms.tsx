import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Save, Trash2 } from "lucide-react";
import { api, Component, GNode, Workspace } from "../api";
import { useToast } from "./ui";
import { useWs } from "../pages/WorkspaceLayout";

/** Plain-data lookup by node pointer (`beginDialog/actions/1/conditions/0`). */
export function getAt(data: unknown, pointer: string): any {
  let cur: any = data;
  for (const p of pointer.split("/").filter(Boolean)) {
    if (cur == null) return undefined;
    cur = Array.isArray(cur) ? cur[Number(p)] : cur[p];
  }
  return cur;
}

/** Variables visible in a topic: Topic.* assigned in it, globals, common System variables. */
export function topicVariables(comp: Component, ws: Workspace): string[] {
  const topic = new Set<string>();
  for (const m of comp.text.matchAll(/(?:init:)?(Topic\.[A-Za-z0-9_]+)/g)) topic.add(m[1]);
  const globals = ws.components.filter((c) => c.category === "variable")
    .map((c) => `Global.${(c.data as { name?: string } | null)?.name ?? c.name}`);
  return [...topic, ...globals, "System.Activity.Text", "System.User.DisplayName", "System.User.Email", "System.Conversation.Id", "System.FallbackCount"];
}

function VarPicker({ vars, onPick, wrap }: { vars: string[]; onPick: (v: string) => void; wrap?: boolean }) {
  return (
    <select className="form-input var-pick" value="" onChange={(e) => { if (e.target.value) onPick(wrap ? `{${e.target.value}}` : e.target.value); }}>
      <option value="">{"{x}"} Insert variable…</option>
      {vars.map((v) => <option key={v} value={v}>{v}</option>)}
    </select>
  );
}

/** Textarea that can insert a variable at the caret. */
function RichText({ value, onChange, vars, rows = 3, placeholder }: { value: string; onChange: (v: string) => void; vars: string[]; rows?: number; placeholder?: string }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const insert = (t: string) => {
    const el = ref.current;
    const at = el?.selectionStart ?? value.length;
    onChange(value.slice(0, at) + t + value.slice(el?.selectionEnd ?? at));
  };
  return (
    <div className="stack" style={{ gap: 4 }}>
      <textarea ref={ref} className="form-input" rows={rows} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      <VarPicker vars={vars} onPick={insert} wrap />
    </div>
  );
}

const ENTITIES = [
  ["BooleanPrebuiltEntity", "Boolean (yes / no)"], ["NumberPrebuiltEntity", "Number"], ["StringPrebuiltEntity", "User's entire response"],
  ["EMailPrebuiltEntity", "Email"], ["DateTimePrebuiltEntity", "Date and time"], ["AgePrebuiltEntity", "Age"],
  ["MoneyPrebuiltEntity", "Money"], ["PercentagePrebuiltEntity", "Percentage"], ["PersonNamePrebuiltEntity", "Person name"],
  ["CityPrebuiltEntity", "City"], ["CountryOrRegionPrebuiltEntity", "Country or region"], ["PhoneNumberPrebuiltEntity", "Phone number"],
  ["URLPrebuiltEntity", "URL"], ["__choice", "Multiple choice options"],
] as const;

type Save = { pointer: string; fields: Record<string, unknown> }[];

function useSave(comp: Component, onSaved?: () => void) {
  const { ws } = useWs();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: async (ops: Save) => {
      let rep = null;
      for (const op of ops) rep = await api.setFields(ws.info.id, comp.id, op.pointer, op.fields);
      return rep;
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["ws", ws.info.id] });
      toast(r && !r.valid ? "Saved — the file has validation errors" : "Saved", r && !r.valid ? "error" : "success");
      onSaved?.();
    },
    onError: (e: Error) => toast(e.message, "error"),
  });
}

export const FORM_KINDS = new Set(["OnRecognizedIntent", "SendActivity", "Question", "ConditionGroup", "SetVariable", "SetTextVariable",
  "BeginDialog", "ReplaceDialog", "InvokeFlowAction"]);

/** Form editor for the common node kinds; returns null when only YAML editing applies. */
export function NodeForm({ comp, node }: { comp: Component; node: GNode }) {
  const { ws } = useWs();
  const data = getAt(comp.data, node.pointer);
  const vars = useMemo(() => topicVariables(comp, ws), [comp, ws]);
  if (!data || typeof data !== "object") return null;
  const key = `${comp.text.length}-${node.pointer}`;      // reset local state when the file changes
  switch (node.kind) {
    case "OnRecognizedIntent": return <TriggerForm key={key} comp={comp} data={data} />;
    case "SendActivity": return <MessageForm key={key} comp={comp} ptr={node.pointer} data={data} vars={vars} />;
    case "Question": return <QuestionForm key={key} comp={comp} ptr={node.pointer} data={data} vars={vars} />;
    case "ConditionGroup": return <ConditionForm key={key} comp={comp} ptr={node.pointer} data={data} vars={vars} />;
    case "SetVariable": case "SetTextVariable": return <SetVarForm key={key} comp={comp} ptr={node.pointer} data={data} vars={vars} />;
    case "BeginDialog": case "ReplaceDialog": return <RedirectForm key={key} comp={comp} ptr={node.pointer} data={data} />;
    case "InvokeFlowAction": return <FlowForm key={key} comp={comp} ptr={node.pointer} data={data} vars={vars} />;
  }
  return null;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <label className="stack" style={{ gap: 4 }}><span className="small">{label}{hint && <span className="tiny faint"> · {hint}</span>}</span>{children}</label>;
}

function SaveBar({ busy, dirty, onSave }: { busy: boolean; dirty: boolean; onSave: () => void }) {
  return <div className="row" style={{ justifyContent: "flex-end" }}>
    <button className="btn btn-primary btn-sm" disabled={!dirty || busy} onClick={onSave}>{busy ? <Loader2 size={14} className="spin" /> : <Save size={14} />}Save</button></div>;
}

function TriggerForm({ comp, data }: { comp: Component; data: any }) {
  const root = comp.data as any;
  const [phrases, setPhrases] = useState<string>(((data.intent?.triggerQueries ?? []) as string[]).join("\n"));
  const [desc, setDesc] = useState<string>(root?.modelDescription ?? "");
  const save = useSave(comp);
  const orig = [((data.intent?.triggerQueries ?? []) as string[]).join("\n"), root?.modelDescription ?? ""];
  return (
    <div className="stack" style={{ gap: 12 }}>
      <Field label="Trigger phrases" hint="one per line, 5–10 recommended">
        <textarea className="form-input" rows={7} value={phrases} onChange={(e) => setPhrases(e.target.value)} /></Field>
      <Field label="Description for generative orchestration" hint="modelDescription — the orchestrator routes on this">
        <textarea className="form-input" rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
      <SaveBar busy={save.isPending} dirty={phrases !== orig[0] || desc !== orig[1]} onSave={() => save.mutate([
        { pointer: "beginDialog", fields: { "intent.triggerQueries": phrases.split("\n").map((s) => s.trim()).filter(Boolean) } },
        { pointer: "", fields: { modelDescription: desc.trim() || null } },
      ])} />
    </div>
  );
}

function variantsOf(act: any): string[] {
  if (typeof act === "string") return [act];
  if (act && Array.isArray(act.text)) return act.text.map(String);
  if (act && typeof act.text === "string") return [act.text];
  return [""];
}

function MessageForm({ comp, ptr, data, vars }: { comp: Component; ptr: string; data: any; vars: string[] }) {
  const orig = variantsOf(data.activity);
  const [variants, setVariants] = useState<string[]>(orig);
  const save = useSave(comp);
  const isObj = data.activity && typeof data.activity === "object";
  const submit = () => {
    const vs = variants.map((v) => v.trimEnd()).filter((v) => v.trim());
    const fields = isObj ? { "activity.text": vs } : { activity: vs.length === 1 ? vs[0] : { text: vs } };
    save.mutate([{ pointer: ptr, fields }]);
  };
  return (
    <div className="stack" style={{ gap: 12 }}>
      {isObj && data.activity.attachments && <div className="small muted">This message also has an adaptive card; edit it in YAML.</div>}
      {variants.map((v, i) => (
        <Field key={i} label={variants.length > 1 ? `Variation ${i + 1}` : "Message"} hint={variants.length > 1 ? "one is picked at random" : "markdown, {Topic.X} inserts a variable"}>
          <div className="row" style={{ alignItems: "flex-start" }}>
            <div className="grow"><RichText value={v} vars={vars} onChange={(t) => setVariants(variants.map((x, j) => (j === i ? t : x)))} /></div>
            {variants.length > 1 && <button className="btn-icon" onClick={() => setVariants(variants.filter((_, j) => j !== i))} aria-label="Remove variation"><Trash2 size={14} /></button>}
          </div>
        </Field>
      ))}
      <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setVariants([...variants, ""])}><Plus size={12} />Add variation</button>
      <SaveBar busy={save.isPending} dirty={JSON.stringify(variants) !== JSON.stringify(orig)} onSave={submit} />
    </div>
  );
}

function QuestionForm({ comp, ptr, data, vars }: { comp: Component; ptr: string; data: any; vars: string[] }) {
  const e = data.entity;
  const embedded = e && typeof e === "object" ? (e.kind === "EmbeddedEntity" ? e.definition : e) : null;
  const initEntity = typeof e === "string" ? e : embedded?.kind === "ClosedListEntity" ? "__choice" : "";
  const initOptions = ((embedded?.items ?? []) as { id?: string; displayName?: string }[]).map((i) => i.displayName ?? i.id ?? "").join("\n");
  const initPrompt = variantsOf(data.prompt)[0] ?? "";
  const [prompt, setPrompt] = useState(initPrompt);
  const [entity, setEntity] = useState<string>(initEntity || "StringPrebuiltEntity");
  const [options, setOptions] = useState(initOptions);
  const [variable, setVariable] = useState<string>(String(data.variable ?? ""));
  const [always, setAlways] = useState<boolean>(!!data.alwaysPrompt);
  const [repeat, setRepeat] = useState<string>(data.repeatCount !== undefined ? String(data.repeatCount) : "");
  const [interrupt, setInterrupt] = useState<boolean>(data.interruptionPolicy?.allowInterruption !== false);
  const save = useSave(comp);
  const complex = e && typeof e === "object" && !["EmbeddedEntity", "ClosedListEntity"].includes(e.kind);
  const submit = () => {
    const fields: Record<string, unknown> = {
      prompt: typeof data.prompt === "object" && data.prompt ? { ...data.prompt, text: [prompt] } : prompt,
      variable, alwaysPrompt: always || null, repeatCount: repeat ? Number(repeat) : null,
      "interruptionPolicy.allowInterruption": interrupt ? null : false,
    };
    if (!complex) {
      fields.entity = entity === "__choice"
        ? { kind: "EmbeddedEntity", definition: { kind: "ClosedListEntity", items: options.split("\n").map((s) => s.trim()).filter(Boolean).map((s) => ({ id: s.replace(/\W+/g, ""), displayName: s })) } }
        : entity;
    }
    save.mutate([{ pointer: ptr, fields }]);
  };
  return (
    <div className="stack" style={{ gap: 12 }}>
      <Field label="Ask a question"><RichText value={prompt} onChange={setPrompt} vars={vars} rows={2} /></Field>
      {complex ? <div className="small muted">Entity <span className="mono">{e.kind}</span> — edit it in YAML.</div> : (
        <Field label="Identify">
          <select className="form-input" value={entity} onChange={(ev) => setEntity(ev.target.value)}>
            {ENTITIES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select></Field>)}
      {entity === "__choice" && !complex && <Field label="Options" hint="one per line, shown as buttons">
        <textarea className="form-input" rows={4} value={options} onChange={(ev) => setOptions(ev.target.value)} /></Field>}
      <Field label="Save user response as" hint="use init: on the first assignment">
        <input className="form-input mono" value={variable} onChange={(ev) => setVariable(ev.target.value)} /></Field>
      <div className="row wrap" style={{ gap: 16 }}>
        <label className="row small"><input type="checkbox" checked={always} onChange={(ev) => setAlways(ev.target.checked)} />Always ask (even if the variable has a value)</label>
        <label className="row small"><input type="checkbox" checked={interrupt} onChange={(ev) => setInterrupt(ev.target.checked)} />Allow switching to another topic</label>
      </div>
      <Field label="Re-prompt count" hint="default 2"><input className="form-input" type="number" min={0} max={5} value={repeat} onChange={(ev) => setRepeat(ev.target.value)} style={{ width: 100 }} /></Field>
      <SaveBar busy={save.isPending} dirty onSave={submit} />
    </div>
  );
}

const OPS = [["=", "is equal to"], ["<>", "is not equal to"], [">", "is greater than"], ["<", "is less than"], ["blank", "is blank"], ["notblank", "is not blank"]];

function ConditionForm({ comp, ptr, data, vars }: { comp: Component; ptr: string; data: any; vars: string[] }) {
  const orig = ((data.conditions ?? []) as { condition?: string }[]).map((c) => String(c.condition ?? ""));
  const [conds, setConds] = useState<string[]>(orig);
  const [b, setB] = useState<{ v: string; op: string; val: string }>({ v: vars[0] ?? "", op: "=", val: "" });
  const save = useSave(comp);
  const build = () => {
    const lit = /^(true|false|-?\d+(\.\d+)?)$/.test(b.val) ? b.val : JSON.stringify(b.val);
    return b.op === "blank" ? `=IsBlank(${b.v})` : b.op === "notblank" ? `=!IsBlank(${b.v})` : `=${b.v} ${b.op} ${lit}`;
  };
  return (
    <div className="stack" style={{ gap: 12 }}>
      {conds.map((c, i) => (
        <Field key={i} label={`Branch ${i + 1} condition`} hint="Power Fx, starts with =">
          <input className="form-input mono" value={c} onChange={(e) => setConds(conds.map((x, j) => (j === i ? e.target.value : x)))} /></Field>
      ))}
      <div className="card stack" style={{ padding: 10 }}>
        <div className="small"><b>Condition builder</b></div>
        <div className="row wrap" style={{ gap: 4 }}>
          <select className="form-input" style={{ flex: "1 1 140px" }} value={b.v} onChange={(e) => setB({ ...b, v: e.target.value })}>{vars.map((v) => <option key={v}>{v}</option>)}</select>
          <select className="form-input" style={{ flex: "1 1 120px" }} value={b.op} onChange={(e) => setB({ ...b, op: e.target.value })}>{OPS.map(([o, l]) => <option key={o} value={o}>{l}</option>)}</select>
          {!["blank", "notblank"].includes(b.op) && <input className="form-input" style={{ flex: "1 1 90px" }} placeholder="value" value={b.val} onChange={(e) => setB({ ...b, val: e.target.value })} />}
        </div>
        <div className="row wrap" style={{ gap: 4 }}>
          <span className="mono tiny faint grow">{build()}</span>
          {conds.map((_, i) => <button key={i} className="btn btn-ghost btn-sm" onClick={() => setConds(conds.map((x, j) => (j === i ? build() : x)))}>Use for branch {i + 1}</button>)}
        </div>
      </div>
      <div className="small faint">Add or remove branches in YAML, or ask the AI Builder.</div>
      <SaveBar busy={save.isPending} dirty={JSON.stringify(conds) !== JSON.stringify(orig)}
               onSave={() => save.mutate(conds.map((c, i) => ({ pointer: `${ptr}/conditions/${i}`, fields: { condition: c } })))} />
    </div>
  );
}

function SetVarForm({ comp, ptr, data, vars }: { comp: Component; ptr: string; data: any; vars: string[] }) {
  const [variable, setVariable] = useState<string>(String(data.variable ?? ""));
  const [value, setValue] = useState<string>(typeof data.value === "string" ? data.value : JSON.stringify(data.value ?? ""));
  const save = useSave(comp);
  return (
    <div className="stack" style={{ gap: 12 }}>
      <Field label="Variable"><input className="form-input mono" value={variable} onChange={(e) => setVariable(e.target.value)} /></Field>
      <Field label="Value" hint={data.kind === "SetTextVariable" ? "text with {variables}" : "literal, or =Power Fx"}>
        <input className="form-input mono" value={value} onChange={(e) => setValue(e.target.value)} /></Field>
      <VarPicker vars={vars} onPick={(v) => setValue(value + (data.kind === "SetTextVariable" ? v : v.replace(/^\{|\}$/g, "")))} wrap={data.kind === "SetTextVariable"} />
      <SaveBar busy={save.isPending} dirty onSave={() => save.mutate([{ pointer: ptr, fields: { variable, value } }])} />
    </div>
  );
}

function RedirectForm({ comp, ptr, data }: { comp: Component; ptr: string; data: any }) {
  const { ws } = useWs();
  const cur = String(data.dialog ?? "");
  const prefix = cur.includes(".topic.") ? cur.split(".topic.")[0]
    : (ws.components.map((c) => c.text.match(/dialog:\s*(\S+)\.topic\./)?.[1]).find(Boolean) ?? "<agentSchemaName>");
  const topics = ws.components.filter((c) => c.category === "topic" && c.id !== comp.id && !c.owner);
  const [dialog, setDialog] = useState(cur);
  const save = useSave(comp);
  return (
    <div className="stack" style={{ gap: 12 }}>
      <Field label={data.kind === "ReplaceDialog" ? "Replace with topic" : "Redirect to topic"}>
        <select className="form-input" value={dialog} onChange={(e) => setDialog(e.target.value)}>
          {!topics.some((t) => `${prefix}.topic.${t.name}` === cur) && <option value={cur}>{cur || "—"}</option>}
          {topics.map((t) => <option key={t.id} value={`${prefix}.topic.${t.name}`}>{t.displayName}</option>)}
        </select></Field>
      <div className="tiny faint mono">{dialog}</div>
      <SaveBar busy={save.isPending} dirty={dialog !== cur} onSave={() => save.mutate([{ pointer: ptr, fields: { dialog } }])} />
    </div>
  );
}

function FlowForm({ comp, ptr, data, vars }: { comp: Component; ptr: string; data: any; vars: string[] }) {
  const { ws } = useWs();
  const flows = ws.components.filter((c) => c.category === "workflow");
  const idOf = (c: Component) => String(((c.data as any)?.metadata ?? {}).workflowId ?? c.name);
  const [flowId, setFlowId] = useState<string>(String(data.flowId ?? ""));
  const flow = flows.find((f) => idOf(f).toLowerCase() === flowId.toLowerCase());
  const { data: info } = useQuery({ queryKey: ["flow-test", ws.info.id, flow?.id], queryFn: () => api.flowTest(ws.info.id, flow!.id), enabled: !!flow });
  const [inB, setInB] = useState<Record<string, string>>({ ...(data.input?.binding ?? {}) });
  const [outB, setOutB] = useState<Record<string, string>>({ ...(data.output?.binding ?? {}) });
  const outs = Object.keys(((flow?.data as any)?.metadata?.outputType?.properties) ?? {});
  const save = useSave(comp);
  return (
    <div className="stack" style={{ gap: 12 }}>
      <Field label="Agent flow">
        <select className="form-input" value={flowId} onChange={(e) => setFlowId(e.target.value)}>
          {!flow && <option value={flowId}>{flowId || "—"}</option>}
          {flows.map((f) => <option key={f.id} value={idOf(f)}>{f.displayName}</option>)}
        </select></Field>
      {info && info.fields.length > 0 && <Field label="Inputs" hint="Power Fx, e.g. =Topic.Title">
        <div className="stack" style={{ gap: 4 }}>{info.fields.map((f) => (
          <div key={f.key} className="row"><span className="small" style={{ width: 110 }}>{f.name}{f.required && " *"}</span>
            <input className="form-input mono grow" list={`vars-${ptr}`} value={inB[f.key] ?? ""} onChange={(e) => setInB({ ...inB, [f.key]: e.target.value })} /></div>))}
          <datalist id={`vars-${ptr}`}>{vars.map((v) => <option key={v} value={`=${v}`} />)}</datalist>
        </div></Field>}
      {outs.length > 0 && <Field label="Save outputs as">
        <div className="stack" style={{ gap: 4 }}>{outs.map((o) => (
          <div key={o} className="row"><span className="small" style={{ width: 110 }}>{o}</span>
            <input className="form-input mono grow" placeholder={`Topic.${o}`} value={outB[o.toLowerCase()] ?? outB[o] ?? ""}
                   onChange={(e) => { const n = { ...outB }; delete n[o]; n[o.toLowerCase()] = e.target.value; setOutB(n); }} /></div>))}</div></Field>}
      <SaveBar busy={save.isPending} dirty onSave={() => save.mutate([{ pointer: ptr, fields: {
        flowId,
        "input.binding": Object.fromEntries(Object.entries(inB).filter(([, v]) => v)),
        "output.binding": Object.keys(outB).some((k) => outB[k]) ? Object.fromEntries(Object.entries(outB).filter(([, v]) => v)) : null,
      } }])} />
    </div>
  );
}
