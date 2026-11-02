// Swarm Studio client. State comes from GET /api/state; live changes from the event stream
// (/api/events, server-sent events). Every API call carries the session token.
(function () {
  'use strict';

  var TOKEN = new URLSearchParams(location.search).get('token') || '';
  var S = null;               // last /api/state snapshot
  var selected = null;        // selected task id
  var sub = 'log';            // log | diff | attempts
  var seq = 0;
  var refreshTimer = null;

  // -- helpers ------------------------------------------------------------------------------

  function $(id) { return document.getElementById(id); }

  function h(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var v = attrs[key];
      if (v === undefined || v === null || v === false) return;
      if (key === 'text') node.textContent = String(v);
      else if (key === 'class') node.className = v;
      else if (key.slice(0, 2) === 'on') node.addEventListener(key.slice(2), v);
      else node.setAttribute(key, v === true ? '' : String(v));
    });
    (children || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return node;
  }

  function api(method, path, body) {
    var opts = { method: method, headers: { 'X-Studio-Token': TOKEN } };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (res) {
      var ctype = res.headers.get('Content-Type') || '';
      var read = ctype.indexOf('json') >= 0 ? res.json() : res.text();
      return read.then(function (data) {
        if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
        return data;
      });
    });
  }

  function post(path, body) {
    return api('POST', path, body || {}).catch(function (err) { alert(err.message); });
  }

  function seconds(s) {
    if (!s) return '';
    s = Math.round(s);
    return s < 90 ? s + 's' : Math.floor(s / 60) + 'm' + String(s % 60).padStart(2, '0') + 's';
  }

  // -- state ----------------------------------------------------------------------------------

  function load() {
    return api('GET', '/api/state').then(function (state) {
      S = state;
      seq = Math.max(seq, state.seq || 0);
      render();
    });
  }

  function refreshSoon() {
    if (refreshTimer) return;
    refreshTimer = setTimeout(function () { refreshTimer = null; load(); }, 150);
  }

  function render() {
    renderBar();
    renderChat();
    renderPlan();
    renderTasks();
    renderPreview();
  }

  // -- top bar ----------------------------------------------------------------------------------

  function setBusy(text) {
    $('busy').hidden = !text;
    $('busy-text').textContent = text || '';
    if (S) S.busy = text;
    updateComposer();
  }

  function renderBar() {
    $('project-name').textContent = S.name;
    $('pill-chief').textContent = 'chief: ' + S.chief;
    $('pill-worker').textContent = 'worker: ' + S.engine;
    var build = $('pill-build');
    build.textContent = 'build: ' + S.build;
    build.className = 'pill build ' + S.build;
    $('btn-pause').hidden = S.build !== 'running';
    var waiting = S.tasks.some(function (t) { return t.status === 'planned'; });
    var blocked = S.tasks.some(function (t) { return t.status === 'failed' || t.status === 'error'; });
    // Any stopped build with planned tasks can resume (e.g. after a failed start), unless a
    // failed task blocks it: that one needs Retry or Skip.
    $('btn-resume').hidden = !(S.build !== 'running' && waiting && !blocked && !S.busy);
    setBusy(S.busy);
    var done = S.tasks.filter(function (t) { return t.status === 'passed'; }).length;
    $('tab-tasks').textContent = S.tasks.length ? 'Tasks ' + done + '/' + S.tasks.length : 'Tasks';
  }

  // -- chat --------------------------------------------------------------------------------------

  function messageNode(m) {
    var who = m.role === 'user' ? 'You' : (m.role === 'chief' ? 'Chief' : m.role);
    var kind = m.kind === 'action' ? 'action' : m.kind;
    var bubble = h('div', { class: 'bubble' }, [m.text]);
    if (m.kind === 'plan' && S.plan && S.plan.version === m.version && S.plan_status === 'proposed') {
      bubble.appendChild(h('div', { class: 'actions' }, [
        h('button', { class: 'small', text: 'Open plan', onclick: function () { showTab('plan'); } }),
        h('button', {
          class: 'small', text: 'Approve',
          disabled: S.plan_problems.length > 0 || !!S.busy,
          onclick: approve
        })
      ]));
    }
    if (m.task) {
      var task = S.tasks.filter(function (t) { return t.id === m.task; })[0];
      var stuck = task && (task.status === 'failed' || task.status === 'error') && m.kind === 'error';
      var skipped = task && task.status === 'skipped' && m.kind === 'error';
      bubble.appendChild(h('div', { class: 'actions' }, [
        h('button', { class: 'small ghost', text: 'Open ' + m.task, onclick: function () { selectTask(m.task); } }),
        stuck || skipped ? h('button', { class: 'small', text: 'Retry', onclick: function () { retryTask(m.task); } }) : null,
        stuck ? h('button', { class: 'small danger', text: 'Skip', onclick: function () { skipTask(m.task); } }) : null
      ]));
    }
    return h('div', { class: 'msg ' + m.role + ' ' + kind }, [
      h('div', { class: 'who', text: who + ' · ' + m.time }), bubble
    ]);
  }

  function renderChat() {
    var box = $('messages');
    var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.textContent = '';
    S.messages.forEach(function (m) { box.appendChild(messageNode(m)); });
    if (atBottom || box.dataset.first !== '1') box.scrollTop = box.scrollHeight;
    box.dataset.first = '1';
    updateComposer();
  }

  function updateComposer() {
    if (!S) return;
    var input = $('input');
    var hints = {
      none: 'Describe the app you want...',
      drafting: 'The chief is writing the plan...',
      applying: 'Writing the plan into the app...',
      proposed: 'Comment on the plan, or approve it...',
      approved: S.build === 'running' ? 'Steer: notes are planned after this build...' : 'Ask for more features...'
    };
    input.placeholder = hints[S.plan_status] || '';
  }

  function send(event) {
    if (event) event.preventDefault();
    var text = $('input').value.trim();
    if (!text) return;
    $('input').value = '';
    post('/api/chat', { text: text });
  }

  function approve() {
    post('/api/plan/approve').then(function () { showTab('tasks'); });
  }

  // -- plan ---------------------------------------------------------------------------------------

  function stepLine(st) {
    var line = st.method + ' ' + st.path;
    if (st.body !== null && st.body !== undefined) line += ' ' + JSON.stringify(st.body);
    line += ' -> ' + st.status;
    if (st.expect !== null && st.expect !== undefined) line += ' ' + JSON.stringify(st.expect);
    return line;
  }

  function routeNode(r) {
    var rows = [h('dt', { text: 'does' }), h('dd', { text: r.does })];
    if (r.body) rows.push(h('dt', { text: 'body' }), h('dd', { text: r.body }));
    rows.push(h('dt', { text: 'reply' }), h('dd', { text: r.reply }));
    r.errors.forEach(function (e) { rows.push(h('dt', { text: 'error' }), h('dd', { text: e })); });
    return h('div', { class: 'route' }, [
      h('div', { class: 'route-head' }, [
        h('span', { class: 'method ' + r.method, text: r.method }), r.path
      ]),
      h('dl', {}, rows)
    ]);
  }

  function featureNode(f, n, task) {
    var head = [n + '. ' + f.name, h('span', { class: 'fn', text: f.title })];
    if (task) head.push(h('span', { class: 'chip ' + task.status, text: task.status }));
    var example = f.scenarios[0];
    var hidden = f.scenarios.slice(1);
    var parts = [h('h3', {}, head)];
    if (f.data) parts.push(h('div', { class: 'data', text: f.data }));
    f.routes.forEach(function (r) { parts.push(routeNode(r)); });
    if (example) {
      parts.push(h('details', { class: 'scenarios', open: true }, [
        h('summary', { text: 'Example the worker sees: ' + example.name }),
        h('div', { class: 'steps', text: example.steps.map(stepLine).join('\n') })
      ]));
    }
    if (hidden.length) {
      parts.push(h('details', { class: 'scenarios' }, [
        h('summary', { text: hidden.length + ' more hidden check(s) (the worker never sees these)' })
      ].concat(hidden.map(function (sc) {
        return h('div', { class: 'steps', text: '# ' + sc.name + '\n' + sc.steps.map(stepLine).join('\n') });
      }))));
    }
    return h('div', { class: 'card' }, parts);
  }

  function sectionNode(s) {
    var calls = [s.action || s.source];
    if (s.toggle) calls.push('toggle ' + s.toggle.field + ': ' + s.toggle.action);
    (s.buttons || []).forEach(function (b) { calls.push(b.label + ': ' + b.action); });
    return h('div', { class: 'section-item' }, [
      h('span', { class: 'chip', text: s.type }), h('b', { text: s.title }), h('code', { text: calls.join(' | ') })
    ]);
  }

  function renderPlan() {
    var view = $('view-plan');
    view.textContent = '';
    if (!S.plan) {
      var text = S.plan_status === 'drafting'
        ? 'The chief is writing the plan. Progress shows in the top bar.'
        : 'No plan yet. Describe your app in the chat.';
      view.appendChild(h('div', { class: 'empty', text: text }));
      return;
    }
    var p = S.plan;
    var tasks = {};
    S.tasks.forEach(function (t) { tasks[t.feature] = t; });
    var status = S.plan_status;
    var body = [h('div', { class: 'plan-head' }, [
      h('h2', { text: p.title }),
      h('span', { class: 'chip', text: 'v' + p.version }),
      h('span', { class: 'chip ' + status, text: status })
    ])];
    if (p.summary) body.push(h('p', { class: 'summary', text: p.summary }));
    if (S.plan_problems.length) {
      body.push(h('div', { class: 'problems' }, [
        h('h3', { text: 'Problems to fix before approval' }),
        h('ul', {}, S.plan_problems.map(function (x) { return h('li', { text: x }); }))
      ]));
    }
    if (status === 'proposed') {
      body.push(h('div', { class: 'plan-actions' }, [
        h('button', { text: 'Approve and build', disabled: S.plan_problems.length > 0 || !!S.busy, onclick: approve }),
        h('span', { class: 'hint', text: 'or write what to change in the chat' })
      ]));
    }
    p.features.forEach(function (f, i) { body.push(featureNode(f, i + 1, tasks[f.name])); });
    var sections = (p.ui && p.ui.sections) || [];
    body.push(h('div', { class: 'card' }, [
      h('h3', { text: 'Page' }),
      h('div', { class: 'data', text: (p.ui && p.ui.subtitle) || '' }),
      h('div', { class: 'section-list' }, sections.map(sectionNode))
    ]));
    view.appendChild(h('div', { class: 'plan' }, body));
  }

  // -- tasks ----------------------------------------------------------------------------------------

  function renderTasks() {
    var list = $('task-list');
    list.textContent = '';
    if (!S.tasks.length) {
      list.appendChild(h('div', { class: 'empty', text: 'Tasks appear here once you approve a plan.' }));
      $('task-detail').hidden = true;
      return;
    }
    S.tasks.forEach(function (t) {
      var attempts = t.max_attempts ? t.attempts + '/' + t.max_attempts : '';
      list.appendChild(h('div', {
        class: 'task-row' + (t.id === selected ? ' selected' : ''),
        onclick: function () { selectTask(t.id); }
      }, [
        h('div', {}, [h('div', { class: 'title', text: t.id }), h('div', { class: 'sub', text: t.title })]),
        h('span', { class: 'meta', text: attempts ? 'attempt ' + attempts : '' }),
        h('span', { class: 'meta', text: seconds(t.seconds) }),
        h('span', { class: 'chip ' + t.status, text: t.status })
      ]));
    });
    if (!selected) {
      var running = S.tasks.filter(function (t) { return t.status === 'running'; })[0];
      if (running) selectTask(running.id);
    } else {
      renderDetailHead();
      // The diff and attempts views have no live events: refetch when the task moves on.
      if (sub !== 'log' && taskSignature(selected) !== detailSignature) loadDetail();
    }
  }

  function taskSignature(tid) {
    var t = S.tasks.filter(function (x) { return x.id === tid; })[0];
    return t ? [t.status, t.attempts, t.runs.length].join('|') : '';
  }

  function retryTask(tid) { post('/api/tasks/' + tid + '/retry'); }

  function skipTask(tid) {
    if (confirm('Skip ' + tid + '? Later features that use its routes will fail their hidden tests. You can retry it later.')) {
      post('/api/tasks/' + tid + '/skip');
    }
  }

  function renderDetailHead() {
    var t = S.tasks.filter(function (x) { return x.id === selected; })[0];
    if (!t) return;
    var head = $('detail-head');
    head.textContent = '';
    head.appendChild(h('b', { text: t.id }));
    head.appendChild(h('span', { class: 'chip ' + t.status, text: t.status }));
    if (t.branch) head.appendChild(h('code', { class: 'fn', text: t.branch }));
    if (t.status === 'failed' || t.status === 'error' || t.status === 'skipped') {
      head.appendChild(h('button', { class: 'small', text: 'Retry', onclick: function () { retryTask(t.id); } }));
    }
    if (t.status === 'failed' || t.status === 'error') {
      head.appendChild(h('button', { class: 'small danger', text: 'Skip', onclick: function () { skipTask(t.id); } }));
    }
    if (t.note) head.appendChild(h('div', { class: 'note', text: t.note }));
  }

  function selectTask(tid) {
    selected = tid;
    showTab('tasks');
    $('task-detail').hidden = false;
    renderTasks();
    loadDetail();
  }

  // Live log: each event carries its line number n. While the full log is being fetched,
  // events are buffered; afterwards only lines past the fetched ones are appended.
  var logLines = 0;       // lines of the selected task's log shown so far
  var logBuffer = null;   // events that arrived during a fetch (null: no fetch running)
  var detailSignature = '';

  function appendLog(d) {
    if (d.n <= logLines) return;  // already in the fetched text
    if (d.n > logLines + 1) { loadDetail(); return; }  // a gap (e.g. a reconnect): resync
    var pre = $('detail-log');
    var stick = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 60;
    pre.appendChild(logLine(d.line, d.stream));
    logLines = d.n;
    if (stick) pre.scrollTop = pre.scrollHeight;
  }

  function logLine(text, stream) {
    var cls = stream === 'model' ? 'model' : '';
    if (/=ok\b/.test(text) && !/FAIL/.test(text)) cls = 'ok';
    if (/FAIL|ERROR|CONFLICT/.test(text)) cls = 'bad';
    if (/^(===|---)/.test(text)) cls = 'head';
    return h('span', { class: cls }, [text + '\n']);
  }

  function loadDetail() {
    if (!selected) return;
    var tid = selected;
    detailSignature = taskSignature(tid);
    if (sub === 'log') {
      logBuffer = [];
      api('GET', '/api/tasks/' + tid + '/log').then(function (text) {
        if (tid !== selected) return;
        var pre = $('detail-log');
        pre.textContent = '';
        var lines = text.split('\n');
        if (lines[lines.length - 1] === '') lines.pop();  // the text ends with a newline
        lines.forEach(function (line) {
          if (line) pre.appendChild(logLine(line, line.indexOf('  | ') === 0 ? 'model' : ''));
        });
        logLines = lines.length;
        var buffered = logBuffer || [];
        logBuffer = null;
        buffered.forEach(appendLog);
        pre.scrollTop = pre.scrollHeight;
      }, function () { logBuffer = null; });
    } else if (sub === 'diff') {
      api('GET', '/api/tasks/' + tid + '/diff').then(function (text) {
        if (tid !== selected) return;
        var pre = $('detail-diff');
        pre.textContent = '';
        if (!text) { pre.textContent = 'No changes yet.'; return; }
        text.split('\n').forEach(function (line) {
          var cls = '';
          if (/^diff --git/.test(line)) cls = 'file';
          else if (/^@@/.test(line)) cls = 'hunk';
          else if (/^\+(?!\+\+)/.test(line)) cls = 'add';
          else if (/^-(?!--)/.test(line)) cls = 'del';
          pre.appendChild(cls ? h('span', { class: cls }, [line]) : document.createTextNode(line + '\n'));
        });
      });
    } else {
      api('GET', '/api/tasks/' + tid + '/attempts').then(function (items) {
        if (tid !== selected) return;
        var box = $('detail-attempts');
        box.textContent = '';
        if (!items.length) { box.appendChild(h('div', { class: 'empty', text: 'No attempts yet.' })); return; }
        items.forEach(function (a) {
          var rec = a.record;
          var head = [h('span', { text: 'Attempt ' + a.n })];
          if (rec) {
            head.push(h('span', { class: 'chip ' + (rec.passed ? 'passed' : 'failed'), text: rec.passed ? 'passed' : 'failed' }));
            head.push(h('span', { class: 'fn', text: 'model ' + seconds(rec.agent_seconds) }));
          }
          var stages = rec ? rec.stages.map(function (st) {
            return h('span', { class: 'stage' + (st.ok ? '' : ' bad'), title: st.detail || '', text: st.name.replace('python -m ', '') + (st.detail ? ' (' + st.detail + ')' : '') });
          }) : [];
          if (rec && rec.reverted.length) stages.push(h('span', { class: 'stage bad', text: rec.reverted.length + ' edit(s) reverted' }));
          var files = a.files.map(function (name) {
            return h('button', {
              class: 'small ghost', text: name,
              onclick: function () { openFile(tid, a.run, a.n, name); }
            });
          });
          box.appendChild(h('div', { class: 'attempt' }, [
            h('div', { class: 'attempt-head' }, head),
            h('div', { class: 'stages' }, stages),
            h('div', { class: 'files' }, files)
          ]));
        });
      });
    }
  }

  function openFile(tid, run, n, name) {
    var q = '?run=' + encodeURIComponent(run) + '&n=' + n + '&name=' + encodeURIComponent(name);
    api('GET', '/api/tasks/' + tid + '/file' + q).then(function (text) {
      $('modal-title').textContent = tid + ' · attempt ' + n + ' · ' + name;
      $('modal-body').textContent = text;
      $('modal').hidden = false;
    });
  }

  function showSub(name) {
    sub = name;
    document.querySelectorAll('.subtab').forEach(function (b) { b.classList.toggle('active', b.dataset.sub === name); });
    $('detail-log').hidden = name !== 'log';
    $('detail-diff').hidden = name !== 'diff';
    $('detail-attempts').hidden = name !== 'attempts';
    loadDetail();
  }

  function showTab(name) {
    document.querySelectorAll('.tab').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === name); });
    $('view-plan').hidden = name !== 'plan';
    $('view-tasks').hidden = name !== 'tasks';
  }

  // -- preview -----------------------------------------------------------------------------------------

  var previewSrc = '';

  function renderPreview() {
    var p = S.preview;
    $('preview-url').textContent = p.url;
    $('preview-url').href = p.url;
    $('preview-note').hidden = !p.error;
    $('preview-note').textContent = p.error || '';
    if (p.running && previewSrc !== p.url) {
      previewSrc = p.url;
      $('preview-frame').src = p.url;
    }
  }

  function reloadPreview() {
    if (!S) return;
    previewSrc = S.preview.url;
    $('preview-frame').src = S.preview.url + '?t=' + Date.now();
  }

  // -- live events ---------------------------------------------------------------------------------------

  function connect() {
    var es = new EventSource('/api/events?token=' + encodeURIComponent(TOKEN) + '&since=' + seq);
    es.addEventListener('message', function (e) {
      var m = JSON.parse(e.data);
      seq = Number(e.lastEventId) || seq;
      if (!S) return;
      if (!S.messages.some(function (x) { return x.id === m.id; })) {
        S.messages.push(m);
        renderChat();
      }
      refreshSoon();
    });
    es.addEventListener('state', function (e) { seq = Number(e.lastEventId) || seq; refreshSoon(); });
    es.addEventListener('busy', function (e) {
      seq = Number(e.lastEventId) || seq;
      setBusy(JSON.parse(e.data).busy);
    });
    es.addEventListener('log', function (e) {
      seq = Number(e.lastEventId) || seq;
      var d = JSON.parse(e.data);
      if (d.task !== selected || sub !== 'log') return;
      if (logBuffer) logBuffer.push(d);
      else appendLog(d);
    });
    es.addEventListener('preview', function (e) {
      seq = Number(e.lastEventId) || seq;
      var d = JSON.parse(e.data);
      if (S) { S.preview = d; renderPreview(); }
      if (d.running) reloadPreview();
    });
    es.addEventListener('reset', function () { load(); });
    es.onerror = function () {
      es.close();
      setTimeout(function () { load().then(connect, function () { setTimeout(connect, 2000); }); }, 1500);
    };
  }

  // -- wiring ------------------------------------------------------------------------------------------------

  $('composer').addEventListener('submit', send);
  $('input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) send(e);
  });
  document.querySelectorAll('.tab').forEach(function (b) {
    b.addEventListener('click', function () { showTab(b.dataset.tab); });
  });
  document.querySelectorAll('.subtab').forEach(function (b) {
    b.addEventListener('click', function () { showSub(b.dataset.sub); });
  });
  $('btn-pause').addEventListener('click', function () { post('/api/build/pause'); });
  $('btn-resume').addEventListener('click', function () { post('/api/build/resume'); });
  $('btn-reload').addEventListener('click', reloadPreview);
  $('btn-restart').addEventListener('click', function () { post('/api/preview/restart'); });
  $('modal-close').addEventListener('click', function () { $('modal').hidden = true; });
  $('modal').addEventListener('click', function (e) { if (e.target === $('modal')) $('modal').hidden = true; });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') $('modal').hidden = true; });

  load().then(function () {
    if (S.tasks.length) showTab('tasks');
    connect();
  }, function (err) {
    document.body.textContent = 'Swarm Studio: ' + err.message + '. Open the link printed by `swarm studio`.';
  });
})();
