'use strict';

(() => {
  const DATABASE = 'ii-private-workspace';
  const VERSION = 1;
  const ITERATIONS = 310_000;
  const VERIFIER_ID = '__verifier__';
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  function base64(bytes) {
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
  }

  function bytes(value) {
    const binary = atob(value);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  }

  function requestPromise(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error || new Error('Vault transaction aborted.'));
    });
  }

  class IIVault {
    constructor() {
      this.database = null;
      this.key = null;
      this.configured = false;
    }

    async open() {
      if (!('indexedDB' in window) || !crypto?.subtle) {
        throw new Error('This browser does not provide IndexedDB and Web Crypto.');
      }
      const request = indexedDB.open(DATABASE, VERSION);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains('meta')) database.createObjectStore('meta', { keyPath: 'id' });
        if (!database.objectStoreNames.contains('documents')) database.createObjectStore('documents', { keyPath: 'id' });
      };
      this.database = await requestPromise(request);
      this.database.onversionchange = () => {
        this.lock();
        this.database.close();
      };
      this.configured = Boolean(await this.meta('configuration'));
      return this;
    }

    async meta(id, value) {
      const mode = value === undefined ? 'readonly' : 'readwrite';
      const transaction = this.database.transaction('meta', mode);
      const store = transaction.objectStore('meta');
      if (value === undefined) return requestPromise(store.get(id));
      store.put({ id, ...value });
      await transactionDone(transaction);
      return value;
    }

    async derive(passphrase, salt, iterations) {
      const material = await crypto.subtle.importKey(
        'raw', encoder.encode(passphrase), { name: 'PBKDF2' }, false, ['deriveKey'],
      );
      return crypto.subtle.deriveKey({
        name: 'PBKDF2', salt, iterations, hash: 'SHA-256',
      }, material, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
    }

    async configure(passphrase) {
      if (this.configured) throw new Error('Vault is already configured.');
      if (typeof passphrase !== 'string' || passphrase.length < 10) {
        throw new Error('Use a passphrase with at least 10 characters.');
      }
      const salt = crypto.getRandomValues(new Uint8Array(16));
      this.key = await this.derive(passphrase, salt, ITERATIONS);
      const verifier = await this.encryptRecord(VERIFIER_ID, { value: 'ii-vault-verifier-v1' });
      await this.meta('configuration', {
        schema: 'ii.vault.v1',
        salt: base64(salt),
        iterations: ITERATIONS,
        verifier,
        created_at: new Date().toISOString(),
      });
      this.configured = true;
      return true;
    }

    async unlock(passphrase) {
      const configuration = await this.meta('configuration');
      if (!configuration) return this.configure(passphrase);
      const candidate = await this.derive(
        passphrase,
        bytes(configuration.salt),
        Number(configuration.iterations),
      );
      this.key = candidate;
      try {
        const verifier = await this.decryptRecord(VERIFIER_ID, configuration.verifier);
        if (verifier.value !== 'ii-vault-verifier-v1') throw new Error('Invalid verifier.');
      } catch {
        this.key = null;
        throw new Error('Passphrase did not unlock this workspace.');
      }
      return true;
    }

    lock() {
      this.key = null;
    }

    get unlocked() {
      return Boolean(this.key);
    }

    async encryptRecord(id, value) {
      if (!this.key) throw new Error('Vault is locked.');
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const aad = encoder.encode(`ii.vault.v1:${id}`);
      const plaintext = encoder.encode(JSON.stringify(value));
      const ciphertext = await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv, additionalData: aad, tagLength: 128 },
        this.key,
        plaintext,
      );
      return { schema: 'ii.vault.record.v1', iv: base64(iv), ciphertext: base64(new Uint8Array(ciphertext)) };
    }

    async decryptRecord(id, envelope) {
      if (!this.key) throw new Error('Vault is locked.');
      if (!envelope || envelope.schema !== 'ii.vault.record.v1') throw new Error('Unsupported vault record.');
      const plaintext = await crypto.subtle.decrypt({
        name: 'AES-GCM',
        iv: bytes(envelope.iv),
        additionalData: encoder.encode(`ii.vault.v1:${id}`),
        tagLength: 128,
      }, this.key, bytes(envelope.ciphertext));
      return JSON.parse(decoder.decode(plaintext));
    }

    async put(id, value) {
      const envelope = await this.encryptRecord(id, value);
      const transaction = this.database.transaction('documents', 'readwrite');
      transaction.objectStore('documents').put({ id, envelope, updated_at: Date.now() });
      await transactionDone(transaction);
      return value;
    }

    async get(id) {
      const transaction = this.database.transaction('documents', 'readonly');
      const record = await requestPromise(transaction.objectStore('documents').get(id));
      return record ? this.decryptRecord(id, record.envelope) : null;
    }

    async list(prefix) {
      const transaction = this.database.transaction('documents', 'readonly');
      const records = await requestPromise(transaction.objectStore('documents').getAll());
      const selected = records.filter((record) => record.id.startsWith(prefix));
      const decrypted = [];
      for (const record of selected) {
        decrypted.push({ id: record.id, value: await this.decryptRecord(record.id, record.envelope) });
      }
      return decrypted;
    }

    async delete(id) {
      const transaction = this.database.transaction('documents', 'readwrite');
      transaction.objectStore('documents').delete(id);
      await transactionDone(transaction);
    }
  }

  Object.defineProperty(window, 'IIVault', { value: IIVault, writable: false });
})();
