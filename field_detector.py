"""DOM field detection for FormIntel.

What this file does:
  - Scans the page DOM using JavaScript executed inside the browser
  - Detects every input field type: text, email, tel, number, date,
    range, select, radio groups, checkboxes, textarea, contenteditable
  - Generates a unique CSS selector for each field so it can be
    relocated after page reloads
  - NEW: detect_or_groups() finds OR separators between fields and
    returns which field indices are alternatives to each other
    (e.g. "Mobile OR Loan Account Number")
"""

from __future__ import annotations
from typing import Any
from playwright.sync_api import Page


class FieldDetector:
    """Detect form-like fields and OR groups from the active Playwright page."""

    # ------------------------------------------------------------------
    # Main field detection script (unchanged from before)
    # ------------------------------------------------------------------
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
    } catch (_) { return false; }
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
    if (node === document.body) parts.unshift("body");
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
      if (labels && labels.length > 0) return toText(labels[0].textContent);
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
    const supported = new Set(["text","email","tel","number","password","date",
      "datetime-local","time","month","range","file","hidden","radio","checkbox"]);
    return supported.has(t) ? t : "text";
  };
  const isRequired = (el) => {
    if (el.required) return true;
    if (el.getAttribute("aria-required") === "true") return true;
    const fieldset = el.closest("fieldset");
    if (fieldset && fieldset.hasAttribute("required")) return true;
    const label = getElementLabel(el);
    if (label.includes("*")) return true;
    return false;
  };
  const OTP_HINTS = ["otp", "one time", "one-time", "verification code", "verify code", "sms code", "passcode"];
  const isOtp = (field, node) => {
    const combined = [field.label, field.name, field.id,
      toText(node.getAttribute("placeholder")),
      toText(node.getAttribute("aria-label"))].join(" ").toLowerCase();
    return OTP_HINTS.some(hint => combined.includes(hint));
  };

  // ── Visibility filter ──────────────────────────────────────────────────
  // Returns true if the element is actually rendered and visible on screen.
  const isVisible = (el) => {
    if (!el) return false;
    // offsetParent is null for display:none elements (and position:fixed in some browsers)
    // but we also check getBoundingClientRect for zero-size elements.
    if (el.offsetParent === null) {
      // position:fixed elements have null offsetParent but may still be visible
      const style = window.getComputedStyle(el);
      if (style.position !== 'fixed') return false;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    return true;
  };

  // ── Noise container filter ─────────────────────────────────────────────
  // Returns true if the element lives inside a known non-form area:
  // nav, header, footer, cookie banners, login popovers, newsletter widgets.
  const NOISE_SELECTORS = [
    'nav', 'header', 'footer',
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    '[class*="cookie"]', '[class*="consent"]', '[class*="gdpr"]',
    '[class*="newsletter"]', '[class*="subscribe"]',
    '[class*="login-modal"]', '[class*="signin-modal"]', '[class*="auth-modal"]',
    '[class*="search-bar"]', '[class*="searchbar"]', '[class*="site-search"]',
    '[id*="cookie"]', '[id*="consent"]', '[id*="newsletter"]',
    '[id*="login-modal"]', '[id*="signin"]',
    '[class*="loyalty"]', '[class*="promo"]', '[class*="popup"]',
    '[class*="overlay"]:not([class*="form"])',
  ];
  const isInNoiseContainer = (el) => {
    return NOISE_SELECTORS.some(sel => {
      try { return el.closest(sel) !== null; }
      catch(_) { return false; }
    });
  };

  const fields = [];
  const radioGroups = new Map();
  const checkboxGroups = new Map();
  const seenStandaloneCheckboxes = new Set();
  const nodes = Array.from(document.querySelectorAll(
    'input, select, textarea, div[contenteditable], div[contenteditable="true"]'
  ));
  for (const node of nodes) {
    // Skip invisible elements — catches CSS-hidden inputs, collapsed sections, etc.
    if (!isVisible(node)) continue;
    // Skip elements inside nav, header, footer, cookie banners, login popovers, etc.
    if (isInNoiseContainer(node)) continue;
    const tag = node.tagName.toLowerCase();
    if (tag === "input") {
      const inputType = normalizeInputType(node.getAttribute("type"));
      if (inputType === "hidden") continue;
      if (inputType === "radio") {
        const name = toText(node.getAttribute("name")) || `__radio__${buildUniqueSelector(node)}`;
        if (!radioGroups.has(name)) radioGroups.set(name, { key: name, elements: [] });
        radioGroups.get(name).elements.push(node);
        continue;
      }
      if (inputType === "checkbox") {
        const name = toText(node.getAttribute("name"));
        if (name) {
          if (!checkboxGroups.has(name)) checkboxGroups.set(name, { key: name, elements: [] });
          checkboxGroups.get(name).elements.push(node);
          continue;
        }
      }
    }
    const baseType = tag === "input" ? normalizeInputType(node.getAttribute("type"))
      : tag === "select" ? (node.multiple ? "select-multiple" : "select")
      : tag === "textarea" ? "textarea" : "contenteditable";
    const options = tag === "select"
      ? Array.from(node.options || []).map((opt) => [toText(opt.value), toText(opt.textContent)])
      : null;
    const field = {
      type: baseType,
      label: getElementLabel(node),
      name: toText(node.getAttribute("name")),
      id: toText(node.id),
      required: isRequired(node),
      min: toText(node.getAttribute("min")) || null,
      max: toText(node.getAttribute("max")) || null,
      step: toText(node.getAttribute("step")) || null,
      pattern: toText(node.getAttribute("pattern")) || null,
      options: options && options.length ? options : null,
      selector: buildUniqueSelector(node),
      skip: baseType === "file",
    };
    if (isOtp(field, node)) {
      field.type = "otp";
      field.skip = false;
    }
    // Detect React Select: combobox inputs are often hidden but need container-based filling.
    if (field.type === "text" && node.getAttribute("role") === "combobox") {
      const inputId = toText(node.id);
      if (inputId.includes("react-select")) {
        field.type = "react-select";
        field.react_select_container = inputId.replace(/-input$/, "");
      }
    }
    fields.push(field);
  }
  for (const [, group] of radioGroups) {
    if (!group.elements || group.elements.length === 0) continue;
    const first = group.elements[0];
    const options = group.elements.map((el) => [toText(el.value) || "on", getOptionLabelForChoice(el)]);
    fields.push({
      type: "radio", label: getElementLabel(first),
      name: toText(first.getAttribute("name")), id: toText(first.id),
      required: group.elements.some((el) => isRequired(el)),
      min: null, max: null, step: null, pattern: null, options: options,
      selector: buildUniqueSelector(first), skip: false,
    });
  }
  for (const [, group] of checkboxGroups) {
    if (!group.elements || group.elements.length === 0) continue;
    if (group.elements.length === 1) {
      const single = group.elements[0];
      const signature = buildUniqueSelector(single);
      if (seenStandaloneCheckboxes.has(signature)) continue;
      seenStandaloneCheckboxes.add(signature);
      fields.push({
        type: "checkbox", label: getElementLabel(single),
        name: toText(single.getAttribute("name")), id: toText(single.id),
        required: isRequired(single),
        min: null, max: null, step: null, pattern: null, options: null,
        selector: buildUniqueSelector(single), skip: false,
      });
    } else {
      const first = group.elements[0];
      const options = group.elements.map((el) => [toText(el.value) || "on", getOptionLabelForChoice(el)]);
      fields.push({
        type: "checkbox-group", label: getElementLabel(first),
        name: toText(first.getAttribute("name")), id: toText(first.id),
        required: group.elements.some((el) => isRequired(el)),
        min: null, max: null, step: null, pattern: null, options: options,
        selector: buildUniqueSelector(first), skip: false,
      });
    }
  }
  return fields.map((field, index) => ({ index, ...field }));
}
"""

    # ------------------------------------------------------------------
    # OR group detection script
    # Finds "OR" text separators between fields and returns which
    # field indices are alternatives to each other.
    #
    # Example result for BHFL form:
    # [
    #   {"group_id": 0, "description": "Mobile OR Loan Account",
    #    "before_indices": [0], "after_indices": [1]},
    #   {"group_id": 1, "description": "Date of Birth OR Applicant PAN",
    #    "before_indices": [2], "after_indices": [3]}
    # ]
    # ------------------------------------------------------------------
    _OR_GROUP_SCRIPT = r"""
(detectedFields) => {
    const orGroups = [];
    let groupId = 0;

    // Step 1: Find all text nodes that contain exactly "OR"
    const orElements = [];
    const walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_TEXT,
        null
    );
    let node;
    while (node = walker.nextNode()) {
        const text = node.textContent.trim().toUpperCase();
        if (text === 'OR' || text === '/ OR /' || text === '- OR -') {
            const parent = node.parentElement;
            if (parent && !parent.querySelector('input, select, textarea')) {
                orElements.push(parent);
            }
        }
    }

    // Step 2: For each OR marker, find fields that are spatially
    // ADJACENT to it — within a tight proximity window.
    // This prevents OR markers from accidentally capturing fields
    // from other rows on the same page.
    for (const orEl of orElements) {
        const orRect = orEl.getBoundingClientRect();
        if (orRect.width === 0 && orRect.height === 0) continue;

        const orCenterX = orRect.left + orRect.width / 2;
        const orCenterY = orRect.top + orRect.height / 2;

        // How far (px) a field centre may be from the OR marker to count as its neighbour.
        // Horizontal rows: fields sit left/right within ~200px vertically of the marker.
        // Vertical stacks: fields sit above/below within ~200px horizontally of the marker.
        const PROXIMITY = 200;

        const beforeIndices = [];
        const afterIndices = [];

        for (const field of detectedFields) {
            const el = document.querySelector(field.selector);
            if (!el) continue;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) continue;

            const elCenterX = rect.left + rect.width / 2;
            const elCenterY = rect.top + rect.height / 2;

            const dX = Math.abs(elCenterX - orCenterX);
            const dY = Math.abs(elCenterY - orCenterY);

            // Horizontal layout: field and OR are on the same row (dY small),
            // and field is within PROXIMITY pixels horizontally.
            const isHorizontalNeighbour = dY < 80 && dX < PROXIMITY;

            // Vertical layout: field and OR are in the same column (dX small),
            // and field is within PROXIMITY pixels vertically.
            const isVerticalNeighbour = dX < 200 && dY < PROXIMITY;

            if (isHorizontalNeighbour) {
                if (elCenterX < orCenterX - 20) beforeIndices.push(field.index);
                else if (elCenterX > orCenterX + 20) afterIndices.push(field.index);
            } else if (isVerticalNeighbour) {
                if (elCenterY < orCenterY - 20) beforeIndices.push(field.index);
                else if (elCenterY > orCenterY + 20) afterIndices.push(field.index);
            }
            // Fields outside the proximity window are ignored for this OR marker.
        }

        if (beforeIndices.length > 0 && afterIndices.length > 0) {
            const beforeLabels = detectedFields
                .filter(f => beforeIndices.includes(f.index))
                .map(f => f.label).join(', ');
            const afterLabels = detectedFields
                .filter(f => afterIndices.includes(f.index))
                .map(f => f.label).join(', ');

            orGroups.push({
                group_id: groupId++,
                description: `${beforeLabels} OR ${afterLabels}`,
                before_indices: beforeIndices,
                after_indices: afterIndices,
            });
        }
    }

    return orGroups;
}
"""

    def detect(self, page: Page) -> list[dict[str, Any]]:
        """
        Detect all form fields on the current page.
        Returns a list of field dicts with index, type, label, selector, etc.
        """
        try:
            result = page.evaluate(self._SCRIPT)
            if not isinstance(result, list):
                return []
            normalized: list[dict[str, Any]] = []
            for i, field in enumerate(result):
                if not isinstance(field, dict):
                    continue
                normalized.append({
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
                    "react_select_container": field.get("react_select_container"),
                })
            return normalized
        except Exception as exc:
            print(f"[FieldDetector] Detection error: {exc}")
            return []

    def detect_or_groups(
        self, page: Page, fields: list[dict]
    ) -> list[dict[str, Any]]:
        """
        Detect OR separator groups on the page.
        Requires already-detected fields as input so it can match
        field indices to their screen positions.

        Returns list of OR group dicts:
        [
          {
            "group_id": 0,
            "description": "Mobile Number OR Loan Account Number",
            "before_indices": [0],   <- field indices on left/above OR
            "after_indices": [1]     <- field indices on right/below OR
          },
          ...
        ]

        Empty list = no OR separators found on this page.
        """
        if not fields:
            return []
        try:
            # Pass the detected fields into the JS so it can match
            # selectors to bounding rects
            result = page.evaluate(self._OR_GROUP_SCRIPT, fields)
            if not isinstance(result, list):
                return []
            groups = []
            for g in result:
                if isinstance(g, dict):
                    groups.append({
                        "group_id": int(g.get("group_id", 0)),
                        "description": str(g.get("description", "")),
                        "before_indices": [int(i) for i in g.get("before_indices", [])],
                        "after_indices": [int(i) for i in g.get("after_indices", [])],
                    })
            if groups:
                print(f"[FieldDetector] Detected {len(groups)} OR group(s):")
                for g in groups:
                    print(f"  OR Group {g['group_id']}: {g['description']}")
            return groups
        except Exception as exc:
            print(f"[FieldDetector] OR group detection error: {exc}")
            return []