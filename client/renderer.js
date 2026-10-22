'use strict';

const elements = {
  status: document.getElementById('status'),
  statusText: document.getElementById('statusText'),
  genesisTag: document.getElementById('genesisTag'),
  experts: document.getElementById('expertsValue'),
  shard: document.getElementById('shardValue'),
  latency: document.getElementById('latencyValue'),
  button: document.getElementById('startButton'),
  error: document.getElementById('errorText'),
  coordinator: document.getElementById('coordinatorText'),
};

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes)) return '—';
  if (bytes >= 1024 ** 3) return `${(bytes / (1024 ** 3)).toFixed(1)} GB`;
  return `${Math.max(0.1, bytes / (1024 ** 2)).toFixed(1)} MB`;
}

function render(state) {
  elements.status.dataset.phase = state.phase || 'idle';
  elements.statusText.textContent = state.message || 'Ready to join the network';
  elements.coordinator.textContent = `Coordinator · ${state.coordinatorUrl}`;
  elements.error.textContent = state.error || '';

  if (state.expertsLabel) elements.experts.textContent = state.expertsLabel;
  if (Number.isFinite(state.expertSizeBytes)) elements.shard.textContent = formatFileSize(state.expertSizeBytes);
  if (Number.isFinite(state.latencyMs)) elements.latency.textContent = `${state.latencyMs} ms`;

  if (state.genesisNumber) {
    elements.genesisTag.textContent = state.genesisMember
      ? `Genesis host #${state.genesisNumber}`
      : `Host #${state.genesisNumber}`;
  }

  const busy = ['preparing', 'connecting', 'working'].includes(state.phase);
  elements.button.disabled = busy || state.active;
  elements.button.textContent = busy
    ? state.phase === 'working' ? 'Running a background job…' : 'Setting up your node…'
    : state.active
      ? 'Verified host is active'
      : state.phase === 'error'
        ? 'Try again'
        : 'Become a host';
}

elements.button.addEventListener('click', async () => {
  elements.button.disabled = true;
  try {
    render(await window.iiHost.start());
  } catch (error) {
    render({
      phase: 'error',
      message: 'Could not start hosting',
      coordinatorUrl: 'http://localhost:8787',
      error: error.message,
    });
  }
});

window.iiHost.onStatus(render);
window.iiHost.getState().then(render);
