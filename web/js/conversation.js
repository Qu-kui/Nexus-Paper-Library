/* One backend-owned history per scope. UI subscribers only render their own session. */
import { api } from './api.js';

export class Conversation {
  constructor(scope, transport = api) {
    this.scope = scope;
    this.api = transport;
    this.path = `/ai/conversations/${encodeURIComponent(scope)}`;
    this.data = null;
    this.busy = false;
    this.pending = null;
    this.epoch = 0;
    this.listeners = new Set();
    this.loading = null;
    this.progress = null;
    this.error = null;
    this.lastRequest = null;
    this.pollTimer = null;
    this.stream = null;
    this.progressPending = false;
    this.requestSerial = 0;
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify() {
    for (const listener of this.listeners) listener(this);
  }

  accept(data) {
    if (!this.data || data.id !== this.data.id || data.revision >= this.data.revision) this.data = data;
    this.notify();
    return this.data;
  }

  async load(force = false) {
    if (this.data && !force) return this.data;
    if (this.loading) return this.loading;
    const epoch = this.epoch;
    this.loading = this.api.get(this.path).then(data => {
      if (epoch === this.epoch) this.accept(data);
      return this.data;
    }).finally(() => { this.loading = null; });
    return this.loading;
  }

  async post(path, payload, displayQuestion = '') {
    await this.load();
    if (this.busy) throw new Error('这个会话正在回答，请稍等');
    const epoch = this.epoch;
    this.busy = true;
    this.pending = displayQuestion || payload.question || '';
    this.lastRequest = {path, payload:{...payload}, displayQuestion};
    this.error = null;
    this.progress = {stage:'等待服务响应',reasoning:'',answer:'',state:'running'};
    const generationRevision = this.data.revision + 1;
    const serial = ++this.requestSerial;
    this.watchProgress(this.data.id, generationRevision, epoch, serial);
    // Do not leave an old save/apply action visible while a new draft is being discussed.
    this.data = { ...this.data, draft: {} };
    this.notify();
    try {
      const result = await this.api.post(path, { ...payload, conversation_id:this.data.id, revision:this.data.revision });
      if (epoch === this.epoch && result.conversation) {
        this.error = null;
        this.progress = null;
        this.accept(result.conversation);
      }
      return result;
    } catch (error) {
      if (epoch === this.epoch) {
        this.error = error.message || '请求失败';
        this.progress = {...this.progress,state:'failed'};
        await this.load(true).catch(() => {});
      }
      throw error;
    } finally {
      if (epoch === this.epoch) {
        clearTimeout(this.pollTimer);
        this.closeStream();
        this.busy = false;
        this.pending = null;
        this.notify();
      }
    }
  }

  watchProgress(id, revision, epoch, serial) {
    // SSE 优先：40ms 级别的增量，前端只按 ~60ms 合并渲染，避免一卡一卡。
    if (typeof EventSource === 'undefined') { this.pollProgress(id, revision, epoch, serial); return; }
    this.closeStream();
    const url = `/api/ai/progress/${encodeURIComponent(this.scope)}/stream`
      + `?conversation_id=${encodeURIComponent(id)}&revision=${revision}`;
    let stream;
    try { stream = new EventSource(url); } catch { this.pollProgress(id, revision, epoch, serial); return; }
    this.stream = stream;
    stream.onmessage = (event) => {
      if (epoch !== this.epoch || serial !== this.requestSerial || !this.busy) { this.closeStream(); return; }
      let row;
      try { row = JSON.parse(event.data); } catch { return; }
      if (!row || row.state === 'idle') return;
      this.applyProgress(row);
    };
    stream.addEventListener('done', () => this.closeStream());
    stream.onerror = () => {
      this.closeStream();
      if (epoch === this.epoch && serial === this.requestSerial && this.busy) this.pollProgress(id, revision, epoch, serial);
    };
  }

  closeStream() {
    if (!this.stream) return;
    try { this.stream.close(); } catch { /* ignore */ }
    this.stream = null;
  }

  applyProgress(row) {
    this.progress = row;
    if (this.progressPending) return;
    this.progressPending = true;
    setTimeout(() => {
      this.progressPending = false;
      if (this.busy) this.notify();
    }, 60);
  }

  async pollProgress(id, revision, epoch, serial) {
    try {
      const result = await this.api.get(`/ai/progress/${encodeURIComponent(this.scope)}?conversation_id=${encodeURIComponent(id)}&revision=${revision}`);
      if (epoch !== this.epoch || serial !== this.requestSerial || !this.busy) return;
      if (result.state !== 'idle') {
        this.progress = result;
        this.notify();
      }
    } catch { /* A polling failure must not fail or replace the actual answer. */ }
    if (epoch === this.epoch && serial === this.requestSerial && this.busy) this.pollTimer = setTimeout(() => this.pollProgress(id,revision,epoch,serial),650);
  }

  async stop() {
    if (!this.busy) return;
    const epoch = ++this.epoch;
    clearTimeout(this.pollTimer);
    this.closeStream();
    this.progress = {stage:'停止生成',reasoning:'',answer:'',state:'stopped'};
    this.pending = null;
    this.notify();
    try {
      const result = await this.api.post(`${this.path}/cancel`, {revision:this.data.revision+1});
      if (epoch === this.epoch) this.accept(result);
    } catch (error) {
      if (epoch === this.epoch) await this.load(true).catch(() => {});
      throw error;
    } finally {
      if (epoch === this.epoch) {this.busy=false; this.notify();}
    }
  }

  retry() {
    if (!this.lastRequest || this.busy) return;
    const {path,payload,displayQuestion} = this.lastRequest;
    return this.post(path,payload,displayQuestion);
  }

  async undo() {
    if (!this.data?.undo_available || this.busy) return;
    const result = await this.api.post(`${this.path}/undo`, {revision:this.data.revision});
    this.progress=null;
    this.error=null;
    return this.accept(result);
  }

  async materials(attachments) {
    await this.load();
    const epoch = this.epoch;
    try {
      const result = await this.api.put(`${this.path}/materials`, { attachments, revision:this.data.revision });
      if (epoch === this.epoch) this.accept(result);
      return result;
    } catch (error) {
      if (epoch === this.epoch) await this.load(true).catch(() => {});
      throw error;
    }
  }

  async clear() {
    // Invalidate outstanding loads, posts and uploads immediately.
    const epoch = ++this.epoch;
    clearTimeout(this.pollTimer);
    this.progress=null;
    this.error=null;
    try {
      const result = await this.api.del(this.path);
      if (epoch === this.epoch) this.accept(result);
      return result;
    } catch (error) {
      if (epoch === this.epoch) await this.load(true).catch(() => {});
      throw error;
    } finally {
      if (epoch === this.epoch) {
        this.busy = false;
        this.pending = null;
        this.notify();
      }
    }
  }
}

const sessions = new Map();
export function getConversation(scope) {
  if (!sessions.has(scope)) sessions.set(scope, new Conversation(scope));
  return sessions.get(scope);
}
