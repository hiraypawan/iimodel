'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('iiHost', Object.freeze({
  getState: () => ipcRenderer.invoke('host:get-state'),
  start: () => ipcRenderer.invoke('host:start'),
  showExpert: () => ipcRenderer.invoke('host:show-expert'),
  onStatus: (listener) => {
    if (typeof listener !== 'function') throw new TypeError('Status listener must be a function.');
    const wrapped = (_event, state) => listener(state);
    ipcRenderer.on('host:status', wrapped);
    return () => ipcRenderer.removeListener('host:status', wrapped);
  },
}));
