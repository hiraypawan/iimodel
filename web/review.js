'use strict';

(() => {
  const byId = (id) => document.getElementById(id);
  const state = { teachings: [], selected: null };
  const configuredBase = document.querySelector('meta[name="ii-api-base"]')?.content || '';

  function apiBase() {
    return (sessionStorage.getItem('ii-review-api') || configuredBase).replace(/\/$/, '');
  }

  function token() { return sessionStorage.getItem('ii-review-token') || ''; }
  function reviewer() { return sessionStorage.getItem('ii-reviewer-id') || ''; }

  async function api(route, options = {}) {
    const response = await fetch(`${apiBase()}${route}`, {
      method: options.method || 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${token()}`,
        'X-Reviewer-Id': reviewer(),
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.message || result.error || `HTTP ${response.status}`);
    return result;
  }

  function statusCard(title, subtitle, buttonLabel = null, onClick = null) {
    const card = document.createElement('div');
    card.className = 'status-card';
    const strong = document.createElement('strong');
    strong.textContent = title;
    const span = document.createElement('span');
    span.textContent = subtitle;
    card.append(strong, span);
    if (buttonLabel) {
      const button = document.createElement('button');
      button.className = 'button';
      button.type = 'button';
      button.textContent = buttonLabel;
      button.addEventListener('click', onClick);
      card.append(button);
    }
    return card;
  }

  function renderTeachings() {
    const list = byId('teaching-list');
    list.replaceChildren();
    const visible = state.teachings.filter((item) => item.status !== 'deleted');
    if (!visible.length) {
      list.append(statusCard('Queue is empty', 'No encrypted contribution metadata is waiting.'));
      return;
    }
    for (const item of visible) {
      const button = document.createElement('button');
      button.className = `item${state.selected?.id === item.id ? ' active' : ''}`;
      button.type = 'button';
      const title = document.createElement('strong');
      title.textContent = item.id;
      const meta = document.createElement('span');
      meta.textContent = `${item.status} · ${item.language} · ${item.category}`;
      button.append(title, meta);
      button.addEventListener('click', () => void selectTeaching(item));
      list.append(button);
    }
  }

  function block(label, text) {
    const container = document.createElement('div');
    container.className = 'text-block';
    const heading = document.createElement('label');
    heading.textContent = label;
    const content = document.createElement('span');
    content.textContent = text;
    container.append(heading, content);
    return container;
  }

  async function selectTeaching(item) {
    state.selected = item;
    renderTeachings();
    byId('selection-status').textContent = 'Loading audited plaintext…';
    const area = byId('review-content');
    area.replaceChildren(statusCard('Decrypting for review', 'The content-view event will be written to the audit log.'));
    try {
      const result = await api(`/admin/teachings/${encodeURIComponent(item.id)}/content`);
      const teaching = result.teaching;
      area.replaceChildren();
      const heading = document.createElement('h2');
      heading.textContent = teaching.id;
      const meta = document.createElement('div');
      meta.className = 'meta';
      for (const value of [teaching.language, teaching.category, teaching.privacy_scan.passed ? 'scan passed' : 'scan failed']) {
        const tag = document.createElement('span');
        tag.className = 'tag';
        tag.textContent = value;
        meta.append(tag);
      }
      const notes = document.createElement('textarea');
      notes.rows = 3;
      notes.maxLength = 4000;
      notes.placeholder = 'Reviewer notes (encrypted at rest)';
      const actions = document.createElement('div');
      actions.className = 'review-actions';
      for (const [decision, label, style] of [
        ['approved', 'Approve', 'approve'],
        ['needs_changes', 'Needs changes', ''],
        ['rejected', 'Reject', 'reject'],
      ]) {
        const button = document.createElement('button');
        button.className = `button ${style}`;
        button.type = 'button';
        button.textContent = label;
        button.addEventListener('click', () => void reviewTeaching(item.id, decision, notes.value));
        actions.append(button);
      }
      area.append(heading, meta, block('Prompt', teaching.prompt), block('Correction', teaching.correction), notes, actions);
      byId('selection-status').textContent = item.status;
    } catch (error) {
      area.replaceChildren(statusCard('Could not load contribution', error.message));
      byId('selection-status').textContent = 'Error';
    }
  }

  async function reviewTeaching(id, decision, notes) {
    try {
      await api(`/admin/teachings/${encodeURIComponent(id)}/review`, {
        method: 'POST', body: { decision, notes, reviewer_id: reviewer() },
      });
      await refresh();
      const next = state.teachings.find((item) => item.id === id);
      if (next) await selectTeaching(next);
    } catch (error) {
      byId('selection-status').textContent = error.message;
    }
  }

  function renderDatasets(items) {
    const target = byId('datasets');
    target.replaceChildren();
    if (!items.length) target.append(statusCard('No dataset versions', 'Create a draft from approved IDs.'));
    for (const item of items) {
      target.append(statusCard(
        item.id,
        `${item.status} · ${item.item_count} items${item.content_hash ? ` · ${item.content_hash.slice(0, 10)}…` : ''}`,
        item.status === 'draft' ? 'Seal snapshot' : null,
        item.status === 'draft' ? () => void sealDataset(item.id) : null,
      ));
    }
  }

  function renderRuns(items) {
    const target = byId('runs');
    target.replaceChildren();
    if (!items.length) target.append(statusCard('No training runs', 'Use the orchestration API after sealing a dataset.'));
    for (const item of items) target.append(statusCard(item.id, `${item.status} · ${item.orchestrator}`));
  }

  async function refresh() {
    if (!token()) {
      byId('connection-dialog').showModal();
      return;
    }
    try {
      const [teachings, datasets, runs] = await Promise.all([
        api('/admin/teachings'), api('/admin/datasets'), api('/admin/training-runs'),
      ]);
      state.teachings = teachings.teachings;
      renderTeachings();
      renderDatasets(datasets.datasets);
      renderRuns(runs.runs);
    } catch (error) {
      byId('selection-status').textContent = error.message;
    }
  }

  async function createDataset() {
    const teachingIds = byId('dataset-items').value.split(',').map((item) => item.trim()).filter(Boolean);
    try {
      await api('/admin/datasets', {
        method: 'POST', body: { teaching_ids: teachingIds, created_by: reviewer() },
      });
      byId('dataset-items').value = '';
      await refresh();
    } catch (error) { byId('selection-status').textContent = error.message; }
  }

  async function sealDataset(id) {
    try {
      await api(`/admin/datasets/${encodeURIComponent(id)}/seal`, {
        method: 'POST', body: { approved_by: reviewer() },
      });
      await refresh();
    } catch (error) { byId('selection-status').textContent = error.message; }
  }

  function openConnection() {
    byId('api-base').value = sessionStorage.getItem('ii-review-api') || configuredBase;
    byId('admin-token').value = token();
    byId('reviewer-id').value = reviewer();
    byId('connection-error').textContent = '';
    byId('connection-dialog').showModal();
  }

  function saveConnection(event) {
    event.preventDefault();
    try {
      const input = byId('api-base').value.trim();
      if (input) {
        const url = new URL(input, location.origin);
        const local = url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
        if (url.protocol !== 'https:' && !local) throw new Error('API base must use HTTPS.');
        sessionStorage.setItem('ii-review-api', url.toString().replace(/\/$/, ''));
      } else sessionStorage.removeItem('ii-review-api');
      if (!byId('admin-token').value.trim() || !byId('reviewer-id').value.trim()) throw new Error('Token and reviewer ID are required.');
      sessionStorage.setItem('ii-review-token', byId('admin-token').value.trim());
      sessionStorage.setItem('ii-reviewer-id', byId('reviewer-id').value.trim());
      byId('connection-dialog').close();
      void refresh();
    } catch (error) { byId('connection-error').textContent = error.message; }
  }

  byId('connect').addEventListener('click', openConnection);
  byId('refresh').addEventListener('click', () => void refresh());
  byId('connection-form').addEventListener('submit', saveConnection);
  byId('cancel-connection').addEventListener('click', () => byId('connection-dialog').close());
  byId('create-dataset').addEventListener('click', () => void createDataset());
  void refresh();
})();
