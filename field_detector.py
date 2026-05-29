"""DOM field detection for smart_form_tester."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page


class FieldDetector:
    """Detect form-like fields from the active Playwright page."""

    _SCRIPT = r"""
() => {
  const toText = (value) => {
    if (value === null || value === undefined) return "";
    return String(value).trim();
  };

  const esc = (value) => {
    const text = String(value ?? "");
    if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(text);
    return text.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  };

  const isUniqueForElement = (selector, element) => {
    if (!selector) return false;
    try {
      const nodes = document.querySelectorAll(selector);
      return nodes.length === 1 && nodes[0] === element;
    } catch (_) {
      return false;
    }
  };

  const getNthChildPath = (element) => {
    const parts = [];
    let node = element;
    while (node && node.nodeType === 1 && node !== document.body) {
      const parent = node.parentElement;
      if (!parent) break;
      const children = Array.from(parent.children);
      const index = children.indexOf(node) + 1;
      parts.unshift(`${node.tagName.toLowerCase()}:nth-child(${index})`);
      node = parent;
    }
    if (node === document.body) {
      parts.unshift("body");
    }
    return parts.join(" > ");
  };

  const buildUniqueSelector = (element) => {
    const tag = element.tagName.toLowerCase();
    const id = toText(element.id);
    if (id) {
      const candidate = `#${esc(id)}`;
      if (isUniqueForElement(candidate, element)) return candidate;
    }

    const name = toText(element.getAttribute("name"));
    if (name) {
      const candidate = `[name="${esc(name)}"]`;
      if (isUniqueForElement(candidate, element)) return candidate;
    }

    const attrs = [
      ["type", element.getAttribute("type")],
      ["name", element.getAttribute("name")],
      ["placeholder", element.getAttribute("placeholder")],
      ["aria-label", element.getAttribute("aria-label")],
      ["role", element.getAttribute("role")],
      ["data-testid", element.getAttribute("data-testid")],
      ["data-test", element.getAttribute("data-test")],
    ].filter((item) => toText(item[1]));

    let combo = tag;
    for (const [key, value] of attrs) {
      combo += `[${key}="${esc(value)}"]`;
      if (isUniqueForElement(combo, element)) return combo;
    }

    const classes = Array.from(element.classList || []).filter(Boolean).slice(0, 3);
    if (classes.length > 0) {
      const classCandidate = `${tag}.${classes.map((c) => esc(c)).join(".")}`;
      if (isUniqueForElement(classCandidate, element)) return classCandidate;
    }

    const nthPath = getNthChildPath(element);
    if (isUniqueForElement(nthPath, element)) return nthPath;

    // Last-resort: full hierarchy with :nth-child segments, guaranteed to resolve.
    return nthPath || tag;
  };

  const getElementLabel = (element) => {
    const byFor = () => {
      const id = toText(element.id);
      if (!id) return "";
      const label = document.querySelector(`label[for="${esc(id)}"]`);
      return label ? toText(label.textContent) : "";
    };

    const fromLabelsApi = () => {
      const labels = element.labels;
      if (labels && labels.length > 0) {
        return toText(labels[0].textContent);
      }
      return "";
    };

    const fromWrappingLabel = () => {
      const wrapper = element.closest("label");
      return wrapper ? toText(wrapper.textContent) : "";
    };

    const label =
      byFor() ||
      fromLabelsApi() ||
      toText(element.getAttribute("aria-label")) ||
      toText(element.getAttribute("placeholder")) ||
      fromWrappingLabel() ||
      toText(element.getAttribute("name")) ||
      toText(element.id) ||
      toText(element.getAttribute("title"));
    return label || "unnamed_field";
  };

  const getOptionLabelForChoice = (element) => {
    const fromLabels = element.labels && element.labels.length ? toText(element.labels[0].textContent) : "";
    if (fromLabels) return fromLabels;
    const wrapperLabel = element.closest("label");
    if (wrapperLabel) return toText(wrapperLabel.textContent);
    return toText(element.getAttribute("aria-label")) || toText(element.value) || "option";
  };

  const normalizeInputType = (rawType) => {
    const t = (rawType || "text").toLowerCase();
    const supported = new Set([
      "text",
      "email",
      "tel",
      "number",
      "password",
      "date",
      "datetime-local",
      "time",
      "month",
      "range",
      "file",
      "hidden",
      "radio",
      "checkbox",
    ]);
    return supported.has(t) ? t : "text";
  };

  const fields = [];
  const radioGroups = new Map();
  const checkboxGroups = new Map();
  const seenStandaloneCheckboxes = new Set();

  const nodes = Array.from(
    document.querySelectorAll(
      'input, select, textarea, div[contenteditable], div[contenteditable="true"]'
    )
  );

  for (const node of nodes) {
    const tag = node.tagName.toLowerCase();
    if (tag === "input") {
      const inputType = normalizeInputType(node.getAttribute("type"));
      if (inputType === "hidden") {
        continue;
      }
      if (inputType === "radio") {
        const name = toText(node.getAttribute("name")) || `__radio__${buildUniqueSelector(node)}`;
        if (!radioGroups.has(name)) {
          radioGroups.set(name, { key: name, elements: [] });
        }
        radioGroups.get(name).elements.push(node);
        continue;
      }
      if (inputType === "checkbox") {
        const name = toText(node.getAttribute("name"));
        if (name) {
          if (!checkboxGroups.has(name)) {
            checkboxGroups.set(name, { key: name, elements: [] });
          }
          checkboxGroups.get(name).elements.push(node);
          continue;
        }
      }
    }

    const baseType =
      tag === "input"
        ? normalizeInputType(node.getAttribute("type"))
        : tag === "select"
          ? (node.multiple ? "select-multiple" : "select")
          : tag === "textarea"
            ? "textarea"
            : "contenteditable";

    const options =
      tag === "select"
        ? Array.from(node.options || []).map((opt) => [toText(opt.value), toText(opt.textContent)])
        : null;

    const field = {
      type: baseType,
      label: getElementLabel(node),
      name: toText(node.getAttribute("name")),
      id: toText(node.id),
      required: !!node.required || node.getAttribute("aria-required") === "true",
      min: toText(node.getAttribute("min")) || null,
      max: toText(node.getAttribute("max")) || null,
      step: toText(node.getAttribute("step")) || null,
      pattern: toText(node.getAttribute("pattern")) || null,
      options: options && options.length ? options : null,
      selector: buildUniqueSelector(node),
      skip: baseType === "file",
    };
    fields.push(field);
  }

  for (const [, group] of radioGroups) {
    if (!group.elements || group.elements.length === 0) continue;
    const first = group.elements[0];
    const options = group.elements.map((el) => [toText(el.value) || "on", getOptionLabelForChoice(el)]);
    const field = {
      type: "radio",
      label: getElementLabel(first),
      name: toText(first.getAttribute("name")),
      id: toText(first.id),
      required: group.elements.some((el) => !!el.required || el.getAttribute("aria-required") === "true"),
      min: null,
      max: null,
      step: null,
      pattern: null,
      options: options,
      selector: buildUniqueSelector(first),
      skip: false,
    };
    fields.push(field);
  }

  for (const [, group] of checkboxGroups) {
    if (!group.elements || group.elements.length === 0) continue;
    if (group.elements.length === 1) {
      const single = group.elements[0];
      const signature = buildUniqueSelector(single);
      if (seenStandaloneCheckboxes.has(signature)) continue;
      seenStandaloneCheckboxes.add(signature);
      fields.push({
        type: "checkbox",
        label: getElementLabel(single),
        name: toText(single.getAttribute("name")),
        id: toText(single.id),
        required: !!single.required || single.getAttribute("aria-required") === "true",
        min: null,
        max: null,
        step: null,
        pattern: null,
        options: null,
        selector: buildUniqueSelector(single),
        skip: false,
      });
    } else {
      const first = group.elements[0];
      const options = group.elements.map((el) => [toText(el.value) || "on", getOptionLabelForChoice(el)]);
      fields.push({
        type: "checkbox-group",
        label: getElementLabel(first),
        name: toText(first.getAttribute("name")),
        id: toText(first.id),
        required: group.elements.some((el) => !!el.required || el.getAttribute("aria-required") === "true"),
        min: null,
        max: null,
        step: null,
        pattern: null,
        options: options,
        selector: buildUniqueSelector(first),
        skip: false,
      });
    }
  }

  return fields.map((field, index) => ({ index, ...field }));
}
"""

    def detect(self, page: Page) -> list[dict[str, Any]]:
        """Return detected fields on the current page."""
        try:
            result = page.evaluate(self._SCRIPT)
            if not isinstance(result, list):
                return []
            normalized: list[dict[str, Any]] = []
            for i, field in enumerate(result):
                if not isinstance(field, dict):
                    continue
                normalized.append(
                    {
                        "index": i,
                        "type": str(field.get("type", "")),
                        "label": str(field.get("label", "")),
                        "name": str(field.get("name", "")),
                        "id": str(field.get("id", "")),
                        "required": bool(field.get("required", False)),
                        "min": field.get("min"),
                        "max": field.get("max"),
                        "step": field.get("step"),
                        "pattern": field.get("pattern"),
                        "options": field.get("options"),
                        "selector": str(field.get("selector", "")),
                        "skip": bool(field.get("skip", False)),
                    }
                )
            return normalized
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[FieldDetector] Detection error: {exc}")
            return []
