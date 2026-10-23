'use strict';

(() => {
  const elements = Object.fromEntries([
    'network-dot', 'network-label', 'install-button', 'lock-button', 'settings-button',
    'new-chat', 'conversation-list', 'chat-title', 'chat-subtitle', 'mode-local',
    'mode-background', 'mode-note', 'messages', 'empty-state', 'composer', 'prompt',
    'voice-button', 'capabilities', 'notify-button', 'job-list', 'unlock-modal',
    'unlock-form', 'unlock-copy', 'vault-passphrase', 'unlock-error', 'settings-modal',
    'settings-form', 'api-base', 'api-token', 'manifest-id', 'settings-error',
    'settings-cancel', 'toast',
  ].map((id) => [id, document.getElementById(id)]));

  const state = {
    vault: new window.IIVault(),
    conversations: [],
    activeConversationId: null,
    jobs: [],
    mode: 'background',
    installPrompt: null,
    serviceWorker: null,
    polling: null,
    speech: null,
    media: null,
    microphoneStream: null,
    voiceHeld: false,
    voiceGeneration: 0,
    apiToken: '',
  };

  const API_META = document.querySelector('meta[name="ii-api-base"]')?.content || '';
  const VAPID_PUBLIC_KEY = document.querySelector('meta[name="ii-vapid-public-key"]')?.content || '';

  function randomId(prefix) {
    return `${prefix}_${crypto.randomUUID()}`;
  }

  function toast(message, timeout = 3_800) {
    elements.toast.textContent = message;
    elements.toast.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { elements.toast.hidden = true; }, timeout);
  }

  function safeApiBase(value) {
    if (!value) return '';
    const parsed = new URL(value, location.origin);
    const localhost = parsed.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
    if (parsed.protocol !== 'https:' && !localhost) throw new Error('Use HTTPS (localhost HTTP is allowed for development).');
    if (parsed.username || parsed.password) throw new Error('Do not put credentials in the API URL.');
    parsed.hash = '';
    parsed.search = '';
    return parsed.toString().replace(/\/$/, '');
  }

  function apiBase() {
    return safeApiBase(localStorage.getItem('ii-api-base') || API_META || '');
  }

  function apiUrl(route) {
    return `${apiBase()}${route}`;
  }

  function userToken() {
    return state.apiToken;
  }

  function manifestId() {
    return localStorage.getItem('ii-worker-manifest') || '';
  }

  async function api(route, { method = 'GET', body, token = userToken() } = {}) {
    const response = await fetch(apiUrl(route), {
      method,
      headers: {
        Accept: 'application/json',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const result = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result?.message || result?.error || `Request failed with HTTP ${response.status}.`);
    return result;
  }

  function formatTime(value) {
    return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(new Date(value));
  }

  function activeConversation() {
    return state.conversations.find((item) => item.id === state.activeConversationId) || null;
  }

  async function persistConversation(conversation) {
    conversation.updated_at = new Date().toISOString();
    await state.vault.put(`conversation:${conversation.id}`, conversation);
  }

  async function loadVaultDocuments() {
    const [conversations, jobs] = await Promise.all([
      state.vault.list('conversation:'),
      state.vault.list('job:'),
    ]);
    state.conversations = conversations.map((entry) => entry.value)
      .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
    state.jobs = jobs.map((entry) => entry.value)
      .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));
    state.activeConversationId = state.conversations[0]?.id || null;
    render();
    startPolling();
  }

  async function createConversation() {
    if (!state.vault.unlocked) return showUnlock();
    const conversation = {
      id: randomId('conversation'),
      title: 'New conversation',
      messages: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    state.conversations.unshift(conversation);
    state.activeConversationId = conversation.id;
    await persistConversation(conversation);
    render();
    elements.prompt.focus();
  }

  function renderConversationList() {
    elements['conversation-list'].replaceChildren();
    for (const conversation of state.conversations) {
      const button = document.createElement('button');
      button.className = `conversation-item${conversation.id === state.activeConversationId ? ' active' : ''}`;
      button.type = 'button';
      const title = document.createElement('strong');
      title.textContent = conversation.title;
      const time = document.createElement('span');
      time.textContent = `${conversation.messages.length} messages · ${formatTime(conversation.updated_at)}`;
      button.append(title, time);
      button.addEventListener('click', () => {
        state.activeConversationId = conversation.id;
        render();
      });
      elements['conversation-list'].append(button);
    }
  }

  function renderMessages() {
    const conversation = activeConversation();
    elements.messages.replaceChildren();
    if (!conversation || !conversation.messages.length) {
      const empty = elements['empty-state'].cloneNode(true);
      empty.hidden = false;
      elements.messages.append(empty);
      elements['chat-title'].textContent = conversation?.title || 'Private workspace';
      elements['chat-subtitle'].textContent = state.vault.unlocked
        ? 'Messages stay encrypted in this browser'
        : 'Unlock to load encrypted conversations';
      return;
    }
    elements['chat-title'].textContent = conversation.title;
    elements['chat-subtitle'].textContent = `${conversation.messages.length} encrypted messages`;
    for (const item of conversation.messages) {
      const message = document.createElement('article');
      message.className = `message ${item.role}`;
      const text = document.createElement('span');
      text.textContent = item.content;
      const meta = document.createElement('span');
      meta.className = 'message-meta';
      meta.textContent = `${item.role} · ${formatTime(item.created_at)}${item.mode ? ` · ${item.mode}` : ''}`;
      message.append(text, meta);
      elements.messages.append(message);
    }
    elements.messages.scrollTop = elements.messages.scrollHeight;
  }

  function renderJobs() {
    elements['job-list'].replaceChildren();
    if (!state.jobs.length) {
      const empty = document.createElement('div');
      empty.className = 'job';
      empty.innerHTML = '<p>No encrypted job receipts loaded.</p>';
      elements['job-list'].append(empty);
      return;
    }
    for (const item of state.jobs.slice(0, 20)) {
      const job = document.createElement('article');
      job.className = 'job';
      const top = document.createElement('div');
      top.className = 'job-top';
      const id = document.createElement('span');
      id.className = 'job-id';
      id.textContent = item.id;
      const status = document.createElement('span');
      status.textContent = item.status;
      top.append(id, status);
      const copy = document.createElement('p');
      copy.textContent = item.error || `Submitted ${formatTime(item.created_at)} · receipt encrypted locally`;
      job.append(top, copy);
      elements['job-list'].append(job);
    }
  }

  function renderMode() {
    elements['mode-local'].classList.toggle('active', state.mode === 'local');
    elements['mode-background'].classList.toggle('active', state.mode === 'background');
    elements['mode-note'].textContent = state.mode === 'local'
      ? 'Local inference runs only through a registered foreground adapter; unavailable adapters fail closed.'
      : 'Background jobs continue at the coordinator; status checks resume while the app is open.';
  }

  function render() {
    renderConversationList();
    renderMessages();
    renderJobs();
    renderMode();
  }

  async function appendMessage(conversation, role, content, mode = null) {
    conversation.messages.push({
      id: randomId('message'), role, content, mode, created_at: new Date().toISOString(),
    });
    if (role === 'user' && conversation.messages.filter((item) => item.role === 'user').length === 1) {
      conversation.title = content.slice(0, 54) || 'New conversation';
    }
    await persistConversation(conversation);
    state.conversations.sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
    render();
  }

  function localAdapter() {
    const adapter = window.iiLocalModelAdapter;
    return adapter && typeof adapter.infer === 'function' ? adapter : null;
  }

  Object.defineProperty(window, 'registerIILocalModelAdapter', {
    value: (adapter) => {
      if (!adapter || typeof adapter.infer !== 'function' || typeof adapter.id !== 'string') {
        throw new Error('A local adapter needs a stable id and infer function.');
      }
      if (window.iiLocalModelAdapter) throw new Error('A local adapter is already registered.');
      Object.defineProperty(window, 'iiLocalModelAdapter', { value: Object.freeze(adapter), configurable: false });
      void detectCapabilities();
    },
    writable: false,
  });

  async function submitLocal(conversation, prompt) {
    const adapter = localAdapter();
    if (!adapter) {
      throw new Error('No foreground local-model adapter is installed. Local inference is unavailable.');
    }
    if (document.visibilityState !== 'visible') throw new Error('Local browser inference requires the app to remain in the foreground.');
    const result = await adapter.infer({ prompt, maxTokens: 512 });
    const text = typeof result === 'string' ? result : result?.text;
    if (!text) throw new Error('The local adapter returned no text.');
    await appendMessage(conversation, 'assistant', text, `local:${adapter.id}`);
  }

  async function submitBackground(conversation, prompt) {
    const token = userToken();
    const manifest = manifestId();
    if (!token || !/^[a-f0-9]{64}$/.test(manifest)) {
      showSettings();
      throw new Error('Add a user token and signed worker manifest in Settings.');
    }
    const result = await api('/api/jobs', {
      method: 'POST',
      token,
      body: { manifest_id: manifest, prompt, max_tokens: 512, hedge_after_ms: 2_000 },
    });
    const job = {
      id: result.id,
      access_token: result.access_token,
      conversation_id: conversation.id,
      status: result.status,
      delivered: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    state.jobs.unshift(job);
    await state.vault.put(`job:${job.id}`, job);
    await appendMessage(conversation, 'system', `Background job ${job.id} submitted. Its receipt is encrypted in this browser.`, 'background');
    renderJobs();
    startPolling();
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!state.vault.unlocked) return showUnlock();
    const prompt = elements.prompt.value.trim();
    if (!prompt) return;
    let conversation = activeConversation();
    if (!conversation) {
      await createConversation();
      conversation = activeConversation();
    }
    elements.prompt.value = '';
    resizeComposer();
    await appendMessage(conversation, 'user', prompt, state.mode);
    elements.prompt.disabled = true;
    try {
      if (state.mode === 'local') await submitLocal(conversation, prompt);
      else await submitBackground(conversation, prompt);
    } catch (error) {
      await appendMessage(conversation, 'system', error.message, state.mode);
      toast(error.message, 5_000);
    } finally {
      elements.prompt.disabled = false;
      elements.prompt.focus();
    }
  }

  async function pollJobs() {
    if (!state.vault.unlocked || document.visibilityState !== 'visible' || !navigator.onLine) return;
    for (const job of state.jobs.filter((item) => ['queued', 'leased'].includes(item.status)).slice(0, 8)) {
      try {
        const result = await api(`/api/jobs/${encodeURIComponent(job.id)}`, { token: job.access_token });
        const current = result.job;
        const changed = current.status !== job.status;
        job.status = current.status;
        job.error = current.error;
        job.updated_at = new Date().toISOString();
        if (current.status === 'completed' && current.result && !job.delivered) {
          const conversation = state.conversations.find((item) => item.id === job.conversation_id);
          if (conversation) await appendMessage(conversation, 'assistant', current.result, 'background');
          job.delivered = true;
          await notify(`II job complete`, 'Your background answer is ready.');
        } else if (['failed', 'cancelled'].includes(current.status) && !job.delivered) {
          const conversation = state.conversations.find((item) => item.id === job.conversation_id);
          if (conversation) await appendMessage(conversation, 'system', current.error || `Job ${current.status}.`, 'background');
          job.delivered = true;
          await notify(`II job ${current.status}`, current.error || 'Open II for details.');
        }
        await state.vault.put(`job:${job.id}`, job);
        if (changed) renderJobs();
      } catch (error) {
        job.error = `Status check paused: ${error.message}`;
        renderJobs();
      }
    }
  }

  function startPolling() {
    clearInterval(state.polling);
    if (!state.vault.unlocked) return;
    void pollJobs();
    state.polling = setInterval(() => void pollJobs(), 5_000);
  }

  async function notify(title, body) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    if (state.serviceWorker) {
      await state.serviceWorker.showNotification(title, {
        body, icon: '/icons/icon-192.png', badge: '/icons/icon-192.png',
        tag: 'ii-job-status', data: { url: '/app.html' },
      });
    } else {
      new Notification(title, { body, icon: '/icons/icon-192.png', tag: 'ii-job-status' });
    }
  }

  function urlBase64ToUint8Array(value) {
    const padding = '='.repeat((4 - (value.length % 4)) % 4);
    const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
  }

  async function enableNotifications() {
    if (!('Notification' in window)) return toast('This browser does not support notifications.');
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') return toast('Notification permission was not granted.');
    if (VAPID_PUBLIC_KEY && state.serviceWorker && userToken()) {
      try {
        const subscription = await state.serviceWorker.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
        });
        await api('/api/push-subscriptions', {
          method: 'POST',
          body: subscription.toJSON(),
        });
        toast('Background push subscription registered.');
        return;
      } catch (error) {
        toast(`App notifications enabled; push registration failed: ${error.message}`, 5_000);
        return;
      }
    }
    toast('Completion notifications are enabled while this app can check status. Server push needs VAPID configuration.');
  }

  function capabilityRow(name, detail, status, tone = '') {
    const row = document.createElement('div');
    row.className = 'capability';
    const copy = document.createElement('div');
    const strong = document.createElement('strong');
    strong.textContent = name;
    const description = document.createElement('span');
    description.textContent = detail;
    copy.append(strong, description);
    const badge = document.createElement('i');
    badge.className = `status ${tone}`;
    badge.textContent = status;
    row.append(copy, badge);
    return row;
  }

  async function detectCapabilities() {
    elements.capabilities.replaceChildren();
    let gpuDetail = 'WebGPU is not exposed';
    let gpuAvailable = false;
    if (navigator.gpu) {
      try {
        const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
        gpuAvailable = Boolean(adapter);
        gpuDetail = adapter ? 'Adapter available; model compatibility is separate' : 'No adapter granted';
      } catch { gpuDetail = 'Adapter request failed'; }
    }
    let storageDetail = 'Storage estimate unavailable';
    let storageTone = 'warn';
    if (navigator.storage?.estimate) {
      const estimate = await navigator.storage.estimate();
      const free = Math.max(0, (estimate.quota || 0) - (estimate.usage || 0));
      const persisted = await navigator.storage.persisted?.();
      storageDetail = `${(free / (1024 ** 3)).toFixed(1)} GiB estimated free · ${persisted ? 'persistent' : 'evictable'}`;
      storageTone = free > 1024 ** 3 ? 'yes' : 'warn';
    }
    let microphone = 'prompt';
    try { microphone = (await navigator.permissions?.query({ name: 'microphone' }))?.state || 'prompt'; } catch { /* Safari omits microphone permission query. */ }
    const memory = navigator.deviceMemory ? `${navigator.deviceMemory} GiB browser hint` : 'Not reported by this browser';
    const local = localAdapter();
    elements.capabilities.append(
      capabilityRow('WebGPU', gpuDetail, gpuAvailable ? 'ready' : 'no', gpuAvailable ? 'yes' : 'warn'),
      capabilityRow('Storage', storageDetail, storageTone === 'yes' ? 'ready' : 'limited', storageTone),
      capabilityRow('Microphone', `Permission: ${microphone}; push-to-talk only while open`, microphone, microphone === 'denied' ? 'warn' : 'yes'),
      capabilityRow('Memory', memory, navigator.deviceMemory ? 'reported' : 'unknown', navigator.deviceMemory ? 'yes' : 'warn'),
      capabilityRow('Local model', local ? `Registered adapter: ${local.id}` : 'No adapter registered; local requests fail closed', local ? 'ready' : 'absent', local ? 'yes' : 'warn'),
      capabilityRow('Offline shell', 'Service worker caches app code, not plaintext chats', state.serviceWorker ? 'ready' : 'checking', state.serviceWorker ? 'yes' : 'warn'),
    );
  }

  function resizeComposer() {
    elements.prompt.style.height = 'auto';
    elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 150)}px`;
  }

  async function startVoice() {
    if (state.voiceHeld) return;
    if (document.visibilityState !== 'visible') return toast('Push-to-talk requires the app to be open.');
    state.voiceHeld = true;
    const generation = ++state.voiceGeneration;
    elements['voice-button'].classList.add('recording');
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (Recognition) {
      const speech = new Recognition();
      state.speech = speech;
      speech.interimResults = true;
      speech.continuous = false;
      speech.onresult = (event) => {
        const transcript = [...event.results].map((result) => result[0].transcript).join(' ');
        elements.prompt.value = transcript;
        resizeComposer();
      };
      speech.onerror = (event) => toast(`Speech recognition: ${event.error}`);
      speech.onend = () => { if (state.speech === speech) stopVoice(); };
      try { speech.start(); } catch (error) { toast(error.message); stopVoice(); }
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!state.voiceHeld || generation !== state.voiceGeneration) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      state.microphoneStream = stream;
      state.media = new MediaRecorder(stream);
      state.media.ondataavailable = () => { /* Audio is deliberately discarded without a transcription adapter. */ };
      state.media.onstop = () => toast('Audio was not stored. This browser has no speech transcription interface.');
      state.media.start();
    } catch (error) {
      toast(`Microphone unavailable: ${error.message}`);
      stopVoice();
    }
  }

  function stopVoice() {
    state.voiceHeld = false;
    state.voiceGeneration += 1;
    const speech = state.speech;
    const media = state.media;
    const stream = state.microphoneStream;
    state.speech = null;
    state.media = null;
    state.microphoneStream = null;
    try { speech?.stop(); } catch { /* Already stopped. */ }
    try { if (media?.state === 'recording') media.stop(); } catch { /* Already stopped. */ }
    stream?.getTracks().forEach((track) => track.stop());
    elements['voice-button'].classList.remove('recording');
  }

  function showUnlock() {
    elements['unlock-modal'].hidden = false;
    elements['unlock-copy'].textContent = state.vault.configured
      ? 'Enter your passphrase. It derives the local key and is never sent or saved.'
      : 'Choose a passphrase. It derives a local encryption key and is never sent or saved.';
    setTimeout(() => elements['vault-passphrase'].focus(), 0);
  }

  async function unlock(event) {
    event.preventDefault();
    elements['unlock-error'].textContent = '';
    try {
      await state.vault.unlock(elements['vault-passphrase'].value);
      elements['vault-passphrase'].value = '';
      elements['unlock-modal'].hidden = true;
      await loadVaultDocuments();
      if (!state.conversations.length) await createConversation();
    } catch (error) {
      elements['unlock-error'].textContent = error.message;
    }
  }

  function lock() {
    stopVoice();
    state.vault.lock();
    state.conversations = [];
    state.jobs = [];
    state.activeConversationId = null;
    state.apiToken = '';
    clearInterval(state.polling);
    render();
    showUnlock();
  }

  function showSettings() {
    elements['settings-error'].textContent = '';
    elements['api-base'].value = localStorage.getItem('ii-api-base') || API_META || '';
    elements['api-token'].value = userToken();
    elements['manifest-id'].value = manifestId();
    elements['settings-modal'].hidden = false;
  }

  function saveSettings(event) {
    event.preventDefault();
    elements['settings-error'].textContent = '';
    try {
      const base = safeApiBase(elements['api-base'].value.trim());
      const manifest = elements['manifest-id'].value.trim();
      if (manifest && !/^[a-f0-9]{64}$/.test(manifest)) throw new Error('Manifest ID must be 64 lowercase hexadecimal characters.');
      if (base) localStorage.setItem('ii-api-base', base); else localStorage.removeItem('ii-api-base');
      if (manifest) localStorage.setItem('ii-worker-manifest', manifest); else localStorage.removeItem('ii-worker-manifest');
      state.apiToken = elements['api-token'].value.trim();
      elements['api-token'].value = '';
      elements['settings-modal'].hidden = true;
      toast('Connection settings saved. The access token stays only in memory until you lock or reload.');
    } catch (error) {
      elements['settings-error'].textContent = error.message;
    }
  }

  async function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    try {
      const registration = await navigator.serviceWorker.register('/service-worker.js', { scope: '/' });
      state.serviceWorker = registration;
      navigator.serviceWorker.addEventListener('message', (event) => {
        if (event.data?.type === 'II_RESUME_JOB_POLL') void pollJobs();
      });
      await detectCapabilities();
    } catch (error) {
      console.warn('[ii-pwa] service worker registration failed:', error);
    }
  }

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    state.installPrompt = event;
    elements['install-button'].hidden = false;
  });
  elements['install-button'].addEventListener('click', async () => {
    if (!state.installPrompt) return;
    await state.installPrompt.prompt();
    await state.installPrompt.userChoice;
    state.installPrompt = null;
    elements['install-button'].hidden = true;
  });
  window.addEventListener('appinstalled', () => toast('II was installed on this device.'));
  window.addEventListener('online', () => {
    elements['network-dot'].classList.remove('offline');
    elements['network-label'].textContent = 'online';
    void pollJobs();
  });
  window.addEventListener('offline', () => {
    elements['network-dot'].classList.add('offline');
    elements['network-label'].textContent = 'offline shell';
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void pollJobs();
    else if (state.serviceWorker?.sync) void state.serviceWorker.sync.register('ii-resume-job-poll').catch(() => {});
    if (document.visibilityState !== 'visible') stopVoice();
  });

  elements['unlock-form'].addEventListener('submit', unlock);
  elements['lock-button'].addEventListener('click', lock);
  elements['settings-button'].addEventListener('click', showSettings);
  elements['settings-cancel'].addEventListener('click', () => { elements['settings-modal'].hidden = true; });
  elements['settings-form'].addEventListener('submit', saveSettings);
  elements['new-chat'].addEventListener('click', () => void createConversation());
  elements.composer.addEventListener('submit', (event) => void handleSubmit(event));
  elements.prompt.addEventListener('input', resizeComposer);
  elements.prompt.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      elements.composer.requestSubmit();
    }
  });
  elements['mode-local'].addEventListener('click', () => { state.mode = 'local'; renderMode(); });
  elements['mode-background'].addEventListener('click', () => { state.mode = 'background'; renderMode(); });
  elements['notify-button'].addEventListener('click', () => void enableNotifications());
  elements['voice-button'].addEventListener('pointerdown', (event) => { event.preventDefault(); void startVoice(); });
  elements['voice-button'].addEventListener('pointerup', stopVoice);
  elements['voice-button'].addEventListener('pointercancel', stopVoice);
  elements['voice-button'].addEventListener('keydown', (event) => { if (event.code === 'Space') void startVoice(); });
  elements['voice-button'].addEventListener('keyup', (event) => { if (event.code === 'Space') stopVoice(); });

  async function bootstrap() {
    if (!navigator.onLine) {
      elements['network-dot'].classList.add('offline');
      elements['network-label'].textContent = 'offline shell';
    }
    await registerServiceWorker();
    await detectCapabilities();
    try {
      await state.vault.open();
      showUnlock();
    } catch (error) {
      elements['unlock-error'].textContent = error.message;
      showUnlock();
    }
  }

  void bootstrap();
})();
