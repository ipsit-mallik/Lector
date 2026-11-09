/*
 * dc-lite.js — a tiny offline renderer for Lector's mockup pages.
 *
 * The mockups were authored as Design Component files (an <x-dc> template
 * with {{holes}}, <sc-if>, <sc-for>, and a `class Component extends DCLogic`
 * script). This file renders them in any browser with no network and no
 * build step, so a mockup can be opened from disk (file://) and clicked
 * through. It supports exactly what the mockups use:
 *   - {{path}} holes in text and attributes (a whole-value hole passes the
 *     raw value: functions become event handlers, booleans set properties)
 *   - <sc-if value="{{x}}">, <sc-for list="{{xs}}" as="x"> with $index
 *   - setState / forceUpdate / componentDidMount / componentWillUnmount
 *   - props from the script's data-props defaults, overridable from the URL:
 *       reader-tier1.html?theme=dark&signedPdf=true
 * DOM is patched in place, so CSS animations don't replay on every update.
 *
 * Mockup aid only: not part of the app, never imported by frontend/.
 */
(function () {
  'use strict';
  var hideStyle = document.createElement('style');
  hideStyle.textContent = 'x-dc{display:none!important}';
  document.head.appendChild(hideStyle);

  function DCLogic(props) { this.props = props || {}; this.state = {}; }
  DCLogic.prototype.setState = function (patch) {
    var next = typeof patch === 'function' ? patch(this.state, this.props) : patch;
    this.state = Object.assign({}, this.state, next);
    this.__schedule && this.__schedule();
  };
  DCLogic.prototype.forceUpdate = function () { this.__schedule && this.__schedule(); };
  window.DCLogic = DCLogic;

  var HOLE = /\{\{\s*([^}]+?)\s*\}\}/g;
  var WHOLE = /^\s*\{\{\s*([^}]+?)\s*\}\}\s*$/;

  function lookup(path, scope) {
    path = path.trim();
    if (path === 'true') return true;
    if (path === 'false') return false;
    if (path === 'null') return null;
    if (/^-?\d+(\.\d+)?$/.test(path)) return Number(path);
    if (/^(['"]).*\1$/.test(path)) return path.slice(1, -1);
    var parts = path.split('.');
    var cur;
    for (var i = scope.length - 1; i >= 0; i--) {
      if (scope[i] && Object.prototype.hasOwnProperty.call(scope[i], parts[0])) { cur = scope[i][parts[0]]; break; }
    }
    for (var j = 1; j < parts.length && cur != null; j++) cur = cur[parts[j]];
    return cur;
  }
  function raw(str, scope) {
    var m = WHOLE.exec(str);
    if (m) return lookup(m[1], scope);
    return str.replace(HOLE, function (_, p) { var v = lookup(p, scope); return v == null ? '' : String(v); });
  }

  // Template DOM -> virtual nodes.
  function build(node, scope, out) {
    if (node.nodeType === 3) {
      var t = node.nodeValue;
      if (t.indexOf('{{') >= 0) t = raw(t, scope);
      out.push({ text: t == null ? '' : String(t) });
      return;
    }
    if (node.nodeType !== 1) return;
    var tag = node.localName;
    if (tag === 'helmet') return;
    if (tag === 'sc-if') {
      if (raw(node.getAttribute('value') || '', scope)) kids(node, scope, out);
      return;
    }
    if (tag === 'sc-for') {
      var list = raw(node.getAttribute('list') || '', scope) || [];
      var as = node.getAttribute('as') || 'item';
      for (var i = 0; i < list.length; i++) {
        var frame = { $index: i }; frame[as] = list[i];
        kids(node, scope.concat([frame]), out);
      }
      return;
    }
    var v = { tag: tag, ns: node.namespaceURI, attrs: {}, props: {}, on: {}, children: [] };
    for (var a = 0; a < node.attributes.length; a++) {
      var at = node.attributes[a];
      var name = at.name, val = raw(at.value, scope);
      if (name.slice(0, 2) === 'on' && name.length > 2) {
        if (typeof val === 'function') {
          var ev = name.slice(2).toLowerCase();
          if (ev === 'doubleclick') ev = 'dblclick';
          v.on[ev] = val;
        }
        continue;
      }
      if (name === 'value' && (tag === 'input' || tag === 'textarea' || tag === 'select')) { v.props.value = val == null ? '' : String(val); continue; }
      if (name === 'checked' || name === 'disabled' || name === 'selected') { v.props[name] = val === true || val === 'true' || val === name || val === ''; continue; }
      if (val === undefined || val === null || val === false && name.slice(0, 5) !== 'aria-') continue;
      v.attrs[name] = String(val);
    }
    kids(node, scope, v.children);
    out.push(v);
  }
  function kids(node, scope, out) { for (var c = node.firstChild; c; c = c.nextSibling) build(c, scope, out); }

  // Virtual nodes -> real DOM, patched in place.
  function create(v) {
    if (v.text !== undefined) { v.el = document.createTextNode(v.text); return v.el; }
    var el = v.ns && v.ns !== 'http://www.w3.org/1999/xhtml' ? document.createElementNS(v.ns, v.tag) : document.createElement(v.tag);
    v.el = el;
    el.__on = {};
    update(el, null, v);
    for (var i = 0; i < v.children.length; i++) el.appendChild(create(v.children[i]));
    return el;
  }
  function listener(e) { var f = this.__on && this.__on[e.type]; if (f) f(e); }
  function update(el, o, v) {
    var oa = o ? o.attrs : {}, k;
    for (k in oa) if (!(k in v.attrs)) el.removeAttribute(k);
    for (k in v.attrs) if (oa[k] !== v.attrs[k]) el.setAttribute(k, v.attrs[k]);
    for (k in v.props) if (el[k] !== v.props[k]) el[k] = v.props[k];
    var oon = o ? o.on : {};
    for (k in v.on) { if (!oon[k]) el.addEventListener(k, listener); el.__on[k] = v.on[k]; }
    for (k in oon) if (!v.on[k]) { el.removeEventListener(k, listener); delete el.__on[k]; }
  }
  function patch(parent, olds, news) {
    var i;
    for (i = 0; i < news.length; i++) {
      var o = olds[i], v = news[i];
      if (!o) { parent.appendChild(create(v)); continue; }
      var same = (o.text !== undefined && v.text !== undefined) || (o.tag && o.tag === v.tag && o.ns === v.ns);
      if (!same) { var el = create(v); parent.replaceChild(el, o.el); continue; }
      v.el = o.el;
      if (v.text !== undefined) { if (o.text !== v.text) o.el.nodeValue = v.text; continue; }
      update(v.el, o, v);
      patch(v.el, o.children, v.children);
    }
    for (; i < olds.length; i++) if (olds[i].el && olds[i].el.parentNode === parent) parent.removeChild(olds[i].el);
  }

  function parseQuery() {
    var out = {};
    location.search.replace(/^\?/, '').split('&').forEach(function (kv) {
      if (!kv) return;
      var p = kv.split('='), key = decodeURIComponent(p[0]), val = decodeURIComponent((p[1] || '').replace(/\+/g, ' '));
      out[key] = val === 'true' ? true : val === 'false' ? false : /^-?\d+(\.\d+)?$/.test(val) ? Number(val) : val;
    });
    return out;
  }

  function boot() {
    // Exported pages wrap the markup in <template data-dc> so the browser
    // keeps it inert (no stray SVG parse errors); <x-dc> also works.
    var holder = document.querySelector('template[data-dc]');
    var tpl = holder ? holder.content : document.querySelector('x-dc');
    var script = document.querySelector('script[type="text/x-dc"]');
    if (!tpl || !script) return;
    tpl.querySelectorAll('helmet style, helmet link').forEach(function (s) { document.head.appendChild(s.cloneNode(true)); });
    var defs = {};
    try { defs = JSON.parse(script.getAttribute('data-props') || '{}'); } catch (e) { console.error('data-props', e); }
    var props = {};
    Object.keys(defs).forEach(function (k) { if (k.charAt(0) !== '$' && defs[k] && 'default' in defs[k]) props[k] = defs[k].default; });
    Object.assign(props, parseQuery());
    var Component = new Function('DCLogic', script.textContent + '\n;return Component;')(DCLogic);
    var inst = new Component(props);
    inst.props = props;
    var mount = document.createElement('div');
    mount.className = 'dc-root';
    var anchor = holder || tpl;
    anchor.parentNode.insertBefore(mount, anchor);
    // Keep the template out of the live page, so its unrendered copies of
    // every control can never match a selector or take focus.
    anchor.parentNode.removeChild(anchor);
    script.parentNode.removeChild(script);
    var tree = [], queued = false;
    function render() {
      queued = false;
      var vals = inst.renderVals ? inst.renderVals() : {};
      var next = [];
      kids(tpl, [vals], next);
      patch(mount, tree, next);
      tree = next;
    }
    inst.__schedule = function () { if (!queued) { queued = true; Promise.resolve().then(render); } };
    render();
    if (inst.componentDidMount) inst.componentDidMount();
    window.addEventListener('unload', function () { if (inst.componentWillUnmount) inst.componentWillUnmount(); });
    window.__mockup = inst;
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
