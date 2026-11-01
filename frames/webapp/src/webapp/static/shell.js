// Page shell: renders the sections described by GET /api/_app (from ui.json).
// Section types:
//   form   {title, action: "POST /api/x", fields: [{name, label, input: text|number|checkbox}], submit}
//   list   {title, source: "GET /api/x", items: "key" (omit when the reply is a list),
//           text: "field", detail: ["field", ...],
//           toggle: {field, action: "PATCH /api/x/{id}"}, buttons: [{label, action}]}
//   values {title, source: "GET /api/x"}
// A route that answers 501 (a stub) or 404 (missing) shows "not built yet".
(function () {
  'use strict';

  var views = [];

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === undefined || value === null || value === false) return;
      if (key === 'text') node.textContent = String(value);
      else if (key.slice(0, 2) === 'on') node.addEventListener(key.slice(2), value);
      else if (key === 'checked' || key === 'disabled') node[key] = Boolean(value);
      else node.setAttribute(key, value === true ? '' : String(value));
    });
    (children || []).forEach(function (child) {
      if (child !== null && child !== undefined) node.appendChild(child);
    });
    return node;
  }

  function slug(text) {
    return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }

  async function call(action, item, body) {
    var parts = String(action || '').split(' ');
    var method = parts[0];
    var path = (parts[1] || '').replace(/\{(\w+)\}/g, function (_, key) {
      var value = item && item[key] !== undefined ? item[key] : '';
      return encodeURIComponent(String(value));
    });
    var options = { method: method, headers: {} };
    if (body !== undefined && body !== null) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    try {
      var res = await fetch(path, options);
      var data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      return { status: res.status, data: data };
    } catch (e) {
      return { status: 0, data: { error: 'the app is not reachable' } };
    }
  }

  function notBuilt(status) {
    return status === 501 || status === 404 || status === 405;
  }

  function ok(status) {
    return status >= 200 && status < 300;
  }

  function message(reply) {
    if (notBuilt(reply.status) && !(reply.data && reply.data.error && reply.status === 404)) {
      return 'This part of the app is not built yet.';
    }
    if (reply.data && reply.data.error) return reply.data.error;
    return 'Request failed (' + reply.status + ').';
  }

  function setPending(card, pending) {
    card.classList.toggle('pending', pending);
    var badge = card.querySelector('.badge');
    if (pending && !badge) card.querySelector('h2').appendChild(el('span', { class: 'badge', text: 'not built yet' }));
    if (!pending && badge) badge.remove();
  }

  function refreshAll() {
    views.forEach(function (view) { if (view.refresh) view.refresh(); });
  }

  function formView(section) {
    var error = el('div', { class: 'error', role: 'alert' });
    var inputs = (section.fields || []).map(function (field) {
      var kind = field.input || 'text';
      var input = el('input', {
        type: kind,
        name: field.name,
        'data-testid': 'field-' + field.name,
        step: kind === 'number' ? 'any' : null
      });
      var label = el('label', { class: kind === 'checkbox' ? 'check' : '' }, [
        document.createTextNode(field.label || field.name), input
      ]);
      return { field: field, kind: kind, input: input, label: label };
    });
    var button = el('button', { type: 'submit', text: section.submit || 'Save', 'data-testid': 'submit-' + slug(section.title) });
    var form = el('form', {}, inputs.map(function (i) { return i.label; }).concat([button]));
    var card = el('section', { class: 'card', 'data-testid': 'section-' + slug(section.title) }, [
      el('h2', { text: section.title }), form, error
    ]);
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      var body = {};
      inputs.forEach(function (i) {
        if (i.kind === 'checkbox') body[i.field.name] = i.input.checked;
        else if (i.kind === 'number') body[i.field.name] = i.input.value === '' ? null : Number(i.input.value);
        else body[i.field.name] = i.input.value;
      });
      button.disabled = true;
      var reply = await call(section.action, null, body);
      button.disabled = false;
      error.textContent = ok(reply.status) ? '' : message(reply);
      setPending(card, reply.status === 501);
      if (ok(reply.status)) {
        inputs.forEach(function (i) { if (i.kind !== 'checkbox') i.input.value = ''; });
        refreshAll();
      }
    });
    return { card: card };
  }

  function itemRow(section, item) {
    var done = section.toggle ? Boolean(item[section.toggle.field]) : false;
    var children = [];
    if (section.toggle) {
      children.push(el('input', {
        type: 'checkbox',
        checked: done,
        'aria-label': section.toggle.field,
        'data-testid': 'toggle-' + item.id,
        onchange: async function (event) {
          var body = {};
          body[section.toggle.field] = event.target.checked;
          var reply = await call(section.toggle.action, item, body);
          if (!ok(reply.status)) alert(message(reply));
          refreshAll();
        }
      }));
    }
    var details = (section.detail || []).map(function (key) {
      return item[key] === undefined ? null : el('span', { class: 'detail', text: key + ': ' + item[key] });
    });
    children.push(el('span', { class: 'text' }, [
      document.createTextNode(String(item[section.text || 'id'] !== undefined ? item[section.text || 'id'] : ''))
    ].concat(details)));
    (section.buttons || []).forEach(function (spec) {
      var danger = /^DELETE /.test(spec.action) ? ' danger' : '';
      children.push(el('button', {
        class: 'quiet' + danger,
        type: 'button',
        text: spec.label,
        'data-testid': slug(spec.label) + '-' + item.id,
        onclick: async function () {
          var reply = await call(spec.action, item, null);
          if (!ok(reply.status)) alert(message(reply));
          refreshAll();
        }
      }));
    });
    return el('li', { class: done ? 'done' : '', 'data-testid': 'item-' + item.id }, children);
  }

  function listView(section) {
    var body = el('div', {}, [el('p', { class: 'note', text: 'Loading...' })]);
    var card = el('section', { class: 'card', 'data-testid': 'section-' + slug(section.title) }, [
      el('h2', { text: section.title }), body
    ]);
    async function refresh() {
      var reply = await call(section.source, null, null);
      setPending(card, notBuilt(reply.status));
      body.textContent = '';
      if (!ok(reply.status)) {
        body.appendChild(el('p', { class: 'note', text: message(reply) }));
        return;
      }
      var items = Array.isArray(reply.data) ? reply.data : (reply.data || {})[section.items];
      if (!Array.isArray(items) || items.length === 0) {
        body.appendChild(el('p', { class: 'note', text: Array.isArray(items) ? 'Nothing here yet.' : 'Unexpected reply.' }));
        return;
      }
      body.appendChild(el('ul', { class: 'items' }, items.map(function (item) { return itemRow(section, item); })));
    }
    return { card: card, refresh: refresh };
  }

  function valuesView(section) {
    var body = el('div', {}, [el('p', { class: 'note', text: 'Loading...' })]);
    var card = el('section', { class: 'card', 'data-testid': 'section-' + slug(section.title) }, [
      el('h2', { text: section.title }), body
    ]);
    async function refresh() {
      var reply = await call(section.source, null, null);
      setPending(card, notBuilt(reply.status));
      body.textContent = '';
      if (!ok(reply.status)) {
        body.appendChild(el('p', { class: 'note', text: message(reply) }));
        return;
      }
      var data = reply.data;
      if (data === null || typeof data !== 'object' || Array.isArray(data)) {
        body.appendChild(el('p', { text: JSON.stringify(data) }));
        return;
      }
      var rows = [];
      Object.keys(data).forEach(function (key) {
        var value = data[key];
        rows.push(el('dt', { text: key }));
        rows.push(el('dd', { 'data-testid': 'value-' + key, text: typeof value === 'object' ? JSON.stringify(value) : String(value) }));
      });
      body.appendChild(el('dl', { class: 'values' }, rows));
    }
    return { card: card, refresh: refresh };
  }

  var KINDS = { form: formView, list: listView, values: valuesView };

  async function start() {
    var reply = await call('GET /api/_app', null, null);
    var ui = reply.data || {};
    document.title = ui.title || 'App';
    document.getElementById('app-title').textContent = ui.title || 'App';
    document.getElementById('app-subtitle').textContent = ui.subtitle || '';
    var main = document.getElementById('sections');
    (ui.sections || []).forEach(function (section) {
      var make = KINDS[section.type];
      if (!make) return;
      var view = make(section);
      views.push(view);
      main.appendChild(view.card);
    });
    if (!views.length) main.appendChild(el('p', { class: 'note', text: 'No sections yet.' }));
    refreshAll();
  }

  window.addEventListener('focus', refreshAll);
  start();
})();
