/**
 * Memory AI - 对话前端
 * 类 Claude.ai 风格的对话界面
 * 支持 Gemini 3 思考功能
 */

// ============================================
// DOM Elements
// ============================================
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);

const DOM = {
  // Sidebar
  sidebar: $('#sidebar'),
  sidebarToggle: $('#sidebarToggle'),
  newChatBtn: $('#newChatBtn'),
  refreshSessionsBtn: $('#refreshSessionsBtn'),
  refreshTopicsBtn: $('#refreshTopicsBtn'),
  sessionsList: $('#sessionsList'),
  topicsList: $('#topicsList'),
  settingsBtn: $('#settingsBtn'),
  
  // Main Content
  chatTitle: $('#chatTitle'),
  chatMeta: $('#chatMeta'),
  sessionInfo: $('#sessionInfo'),
  thinkingBadge: $('#thinkingBadge'),
  thinkingText: $('#thinkingText'),
  archiveBadge: $('#archiveBadge'),
  archiveText: $('#archiveText'),
  contextBadge: $('#contextBadge'),
  contextText: $('#contextText'),
  clearChatBtn: $('#clearChatBtn'),
  messagesContainer: $('#messagesContainer'),
  messagesList: $('#messagesList'),
  welcomeScreen: $('#welcomeScreen'),
  messageInput: $('#messageInput'),
  sendBtn: $('#sendBtn'),
  
  // Settings Panel
  settingsPanel: $('#settingsPanel'),
  panelOverlay: $('#panelOverlay'),
  closeSettingsBtn: $('#closeSettingsBtn'),
  apiBaseUrl: $('#apiBaseUrl'),
  userId: $('#userId'),
  healthCheckBtn: $('#healthCheckBtn'),
  healthStatus: $('#healthStatus'),
  thinkingLevel: $('#thinkingLevel'),
  showThinking: $('#showThinking'),
  autoScroll: $('#autoScroll'),
  showTimestamp: $('#showTimestamp'),
  markdownRender: $('#markdownRender'),
  saveSettingsBtn: $('#saveSettingsBtn'),
  
  // Artifact Panel
  artifactPanel: $('#artifactPanel'),
  artifactOverlay: $('#artifactOverlay'),
  closeArtifactBtn: $('#closeArtifactBtn'),
  artifactTitle: $('#artifactTitle'),
  artifactMeta: $('#artifactMeta'),
  artifactContent: $('#artifactContent'),
  
  // Loading
  loadingOverlay: $('#loadingOverlay'),
};

// ============================================
// State Management
// ============================================
const state = {
  currentSessionId: null,
  sessions: [],
  topics: [],
  messages: [],
  isLoading: false,
  settings: {
    apiBaseUrl: 'http://localhost:8000',
    userId: 'user_123',
    thinkingLevel: 'medium',
    showThinking: true,
    autoScroll: true,
    showTimestamp: true,
    markdownRender: true,
  },
};

// ============================================
// Utility Functions
// ============================================
const normalizeUrl = (url) => url.trim().replace(/\/+$/, '');

const formatTime = (isoString) => {
  const date = new Date(isoString);
  const now = new Date();
  const diff = now - date;
  
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
  if (diff < 604800000) return `${Math.floor(diff / 86400000)} 天前`;
  
  return date.toLocaleDateString('zh-CN');
};

const formatFullTime = (isoString) => {
  const date = new Date(isoString);
  return date.toLocaleString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  });
};

const escapeHtml = (text) => {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
};

const renderMarkdown = (text) => {
  if (!state.settings.markdownRender || typeof marked === 'undefined') {
    return escapeHtml(text).replace(/\n/g, '<br>');
  }
  
  try {
    marked.setOptions({
      breaks: true,
      gfm: true,
      headerIds: false,
      mangle: false,
    });
    return marked.parse(text);
  } catch (e) {
    console.error('Markdown parsing error:', e);
    return escapeHtml(text).replace(/\n/g, '<br>');
  }
};

const formatTokenCount = (count) => {
  if (count >= 1000) {
    return `${(count / 1000).toFixed(1)}k`;
  }
  return count.toString();
};

const getThinkingLevelLabel = (level) => {
  const labels = {
    'lowest': '最低',
    'low': '低',
    'medium': '中等',
    'high': '高',
  };
  return labels[level] || level;
};

// ============================================
// API Functions
// ============================================
const api = {
  async request(path, options = {}) {
    const url = `${normalizeUrl(state.settings.apiBaseUrl)}${path}`;
    const init = {
      method: options.method || 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    };
    
    if (options.body) {
      init.body = JSON.stringify(options.body);
    }
    
    const response = await fetch(url, init);
    const text = await response.text();
    let data = null;
    
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (e) {
        data = { raw: text };
      }
    }
    
    if (!response.ok) {
      const detail = data?.detail || text || response.statusText;
      throw new Error(detail);
    }
    
    return data;
  },
  
  async healthCheck() {
    return this.request('/health');
  },
  
  async createSession() {
    return this.request(`/api/sessions/${state.settings.userId}/create`, {
      method: 'POST',
    });
  },
  
  async sendMessage(message, sessionId = null, thinkingLevel = null) {
    const payload = {
      user_id: state.settings.userId,
      message,
      thinking_level: thinkingLevel || state.settings.thinkingLevel,
    };
    
    if (sessionId) {
      payload.session_id = sessionId;
    }
    
    return this.request('/api/chat', {
      method: 'POST',
      body: payload,
    });
  },
  
  async getSessionMessages(sessionId) {
    return this.request(`/api/sessions/${state.settings.userId}/${sessionId}/messages`);
  },
  
  async getTopics(sessionId) {
    return this.request(`/api/sessions/${state.settings.userId}/${sessionId}/topics`);
  },
  
  async getTopicArtifact(sessionId, threadId) {
    return this.request(`/api/sessions/${state.settings.userId}/${sessionId}/topics/${threadId}/artifact`);
  },
  
  async getThinkingConfig() {
    return this.request('/api/thinking/config');
  },
};

// ============================================
// UI Functions
// ============================================
const ui = {
  showLoading(text = '思考中...') {
    DOM.loadingOverlay.querySelector('span').textContent = text;
    DOM.loadingOverlay.classList.add('show');
    state.isLoading = true;
  },
  
  hideLoading() {
    DOM.loadingOverlay.classList.remove('show');
    state.isLoading = false;
  },
  
  toggleSidebar() {
    DOM.sidebar.classList.toggle('open');
  },
  
  openSettings() {
    DOM.settingsPanel.classList.add('open');
  },
  
  closeSettings() {
    DOM.settingsPanel.classList.remove('open');
  },
  
  openArtifact() {
    DOM.artifactPanel.classList.add('open');
  },
  
  closeArtifact() {
    DOM.artifactPanel.classList.remove('open');
  },
  
  updateSessionInfo() {
    if (state.currentSessionId) {
      DOM.sessionInfo.textContent = `会话: ${state.currentSessionId.slice(0, 8)}...`;
      DOM.chatTitle.textContent = '对话中';
    } else {
      DOM.sessionInfo.textContent = '未选择会话';
      DOM.chatTitle.textContent = '新对话';
    }
  },
  
  showThinkingBadge() {
    DOM.thinkingBadge.style.display = 'inline-flex';
    DOM.thinkingBadge.classList.add('active');
    DOM.thinkingText.textContent = '思考中...';
  },
  
  hideThinkingBadge() {
    DOM.thinkingBadge.classList.remove('active');
    DOM.thinkingBadge.style.display = 'none';
  },
  
  updateThinkingBadge(thinking) {
    if (thinking && thinking.enabled) {
      DOM.thinkingBadge.style.display = 'inline-flex';
      DOM.thinkingBadge.classList.remove('active');
      const level = getThinkingLevelLabel(thinking.level);
      DOM.thinkingText.textContent = `思考: ${level}`;
    } else {
      DOM.thinkingBadge.style.display = 'none';
    }
  },
  
  updateBadges(archived, archiveTopic, contextLoads, thinking = null) {
    // 更新思考徽章
    if (thinking) {
      this.updateThinkingBadge(thinking);
    }
    
    // 更新归档徽章
    if (archived) {
      DOM.archiveBadge.style.display = 'inline-flex';
      DOM.archiveBadge.classList.add('active');
      DOM.archiveText.textContent = archiveTopic ? `归档: ${archiveTopic}` : '已归档';
    }
    
    // 更新上下文重构徽章
    if (contextLoads > 0) {
      DOM.contextBadge.style.display = 'inline-flex';
      DOM.contextBadge.classList.add('active');
      DOM.contextText.textContent = `上下文重构: ${contextLoads}`;
    }
  },
  
  resetBadges() {
    DOM.thinkingBadge.style.display = 'none';
    DOM.thinkingBadge.classList.remove('active');
    DOM.archiveBadge.style.display = 'none';
    DOM.archiveBadge.classList.remove('active');
    DOM.contextBadge.style.display = 'none';
    DOM.contextBadge.classList.remove('active');
  },
  
  renderSessionsList() {
    if (state.sessions.length === 0) {
      DOM.sessionsList.innerHTML = `
        <div class="empty-state">
          <p>暂无对话历史</p>
          <p class="hint">点击上方 + 开始新对话</p>
        </div>
      `;
      return;
    }
    
    DOM.sessionsList.innerHTML = state.sessions.map(session => `
      <div class="session-item ${session.session_id === state.currentSessionId ? 'active' : ''}" 
           data-session-id="${session.session_id}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
        </svg>
        <div class="item-content">
          <div class="item-title">会话 ${session.session_id.slice(0, 8)}</div>
          <div class="item-meta">${formatTime(session.created_at)}</div>
        </div>
      </div>
    `).join('');
    
    DOM.sessionsList.querySelectorAll('.session-item').forEach(item => {
      item.addEventListener('click', () => {
        const sessionId = item.dataset.sessionId;
        loadSession(sessionId);
      });
    });
  },
  
  renderTopicsList() {
    if (state.topics.length === 0) {
      DOM.topicsList.innerHTML = `
        <div class="empty-state">
          <p>暂无知识文档</p>
          <p class="hint">对话积累后自动生成</p>
        </div>
      `;
      return;
    }
    
    DOM.topicsList.innerHTML = state.topics.map(topic => `
      <div class="topic-item" data-thread-id="${topic.thread_id}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
          <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>
        </svg>
        <div class="item-content">
          <div class="item-title">${escapeHtml(topic.title)}</div>
          <div class="item-meta">${formatTime(topic.created_at)}</div>
        </div>
      </div>
    `).join('');
    
    DOM.topicsList.querySelectorAll('.topic-item').forEach(item => {
      item.addEventListener('click', () => {
        const threadId = item.dataset.threadId;
        loadArtifact(threadId);
      });
    });
  },
  
  // 添加带思考摘要的消息
  addMessageWithThinking(role, content, thinking = null, timestamp = null) {
    if (DOM.welcomeScreen) {
      DOM.welcomeScreen.style.display = 'none';
    }
    
    const isUser = role === 'user';
    const time = timestamp ? formatFullTime(timestamp) : formatFullTime(new Date().toISOString());
    
    // 构建思考摘要 HTML
    let thinkingSummaryHtml = '';
    if (!isUser && thinking && thinking.summary && state.settings.showThinking) {
      const summaryText = escapeHtml(thinking.summary);
      thinkingSummaryHtml = `
        <div class="thinking-summary collapsed" id="thinking-${Date.now()}">
          <div class="thinking-summary-header">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <path d="M12 6v6l4 2"/>
            </svg>
            <span>思考过程</span>
          </div>
          <div class="thinking-summary-content">${summaryText}</div>
          <button class="thinking-summary-toggle" onclick="toggleThinkingSummary(this)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M6 9l6 6 6-6"/>
            </svg>
            <span>展开</span>
          </button>
          ${thinking.thoughts_tokens > 0 ? `
            <div class="thinking-stats">
              <span class="thinking-stat">
                思考令牌: <span class="thinking-stat-value">${formatTokenCount(thinking.thoughts_tokens)}</span>
              </span>
              <span class="thinking-stat">
                输出令牌: <span class="thinking-stat-value">${formatTokenCount(thinking.output_tokens)}</span>
              </span>
              <span class="thinking-stat">
                层级: <span class="thinking-stat-value">${getThinkingLevelLabel(thinking.level)}</span>
              </span>
            </div>
          ` : ''}
        </div>
      `;
    }
    
    const messageHtml = `
      <div class="message ${role}">
        <div class="message-header">
          <div class="message-avatar">
            ${isUser ? `
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
              </svg>
            ` : `
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                <path d="M2 17l10 5 10-5"/>
                <path d="M2 12l10 5 10-5"/>
              </svg>
            `}
          </div>
          <span class="message-sender">${isUser ? '你' : 'Memory AI'}</span>
          ${state.settings.showTimestamp ? `<span class="message-time">${time}</span>` : ''}
        </div>
        <div class="message-content">
          ${thinkingSummaryHtml}
          ${isUser ? escapeHtml(content) : renderMarkdown(content)}
        </div>
      </div>
    `;
    
    DOM.messagesList.insertAdjacentHTML('beforeend', messageHtml);
    
    if (state.settings.autoScroll) {
      scrollToBottom();
    }
  },
  
  addMessage(role, content, timestamp = null) {
    this.addMessageWithThinking(role, content, null, timestamp);
  },
  
  addTypingIndicator() {
    const html = `
      <div class="message assistant" id="typingIndicator">
        <div class="message-header">
          <div class="message-avatar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 2L2 7l10 5 10-5-10-5z"/>
              <path d="M2 17l10 5 10-5"/>
              <path d="M2 12l10 5 10-5"/>
            </svg>
          </div>
          <span class="message-sender">Memory AI</span>
        </div>
        <div class="typing-indicator">
          <div class="typing-dots">
            <span></span>
            <span></span>
            <span></span>
          </div>
        </div>
      </div>
    `;
    
    DOM.messagesList.insertAdjacentHTML('beforeend', html);
    scrollToBottom();
  },
  
  removeTypingIndicator() {
    const indicator = $('#typingIndicator');
    if (indicator) {
      indicator.remove();
    }
  },
  
  clearMessages() {
    DOM.messagesList.innerHTML = '';
    if (DOM.welcomeScreen) {
      DOM.welcomeScreen.style.display = 'flex';
      DOM.messagesList.appendChild(DOM.welcomeScreen);
    }
  },
  
  renderArtifact(data) {
    DOM.artifactTitle.textContent = data.title;
    DOM.artifactMeta.innerHTML = `
      <strong>主题 ID:</strong> ${data.thread_id}<br>
      <strong>来源映射:</strong> ${Object.keys(data.sources_map || {}).length} 个章节
    `;
    DOM.artifactContent.innerHTML = renderMarkdown(data.artifact || '暂无内容');
  },
};

// ============================================
// Global Functions (for onclick handlers)
// ============================================
window.toggleThinkingSummary = function(button) {
  const summary = button.closest('.thinking-summary');
  const isCollapsed = summary.classList.contains('collapsed');
  
  if (isCollapsed) {
    summary.classList.remove('collapsed');
    button.querySelector('span').textContent = '收起';
  } else {
    summary.classList.add('collapsed');
    button.querySelector('span').textContent = '展开';
  }
};

// ============================================
// Core Functions
// ============================================
function scrollToBottom() {
  DOM.messagesContainer.scrollTo({
    top: DOM.messagesContainer.scrollHeight,
    behavior: 'smooth',
  });
}

function loadSettings() {
  const saved = localStorage.getItem('memoryai_settings');
  if (saved) {
    try {
      const parsed = JSON.parse(saved);
      state.settings = { ...state.settings, ...parsed };
    } catch (e) {
      console.error('Failed to load settings:', e);
    }
  }
  
  // Apply to UI
  DOM.apiBaseUrl.value = state.settings.apiBaseUrl;
  DOM.userId.value = state.settings.userId;
  DOM.thinkingLevel.value = state.settings.thinkingLevel;
  DOM.showThinking.checked = state.settings.showThinking;
  DOM.autoScroll.checked = state.settings.autoScroll;
  DOM.showTimestamp.checked = state.settings.showTimestamp;
  DOM.markdownRender.checked = state.settings.markdownRender;
}

function saveSettings() {
  state.settings.apiBaseUrl = DOM.apiBaseUrl.value.trim() || 'http://localhost:8000';
  state.settings.userId = DOM.userId.value.trim() || 'user_123';
  state.settings.thinkingLevel = DOM.thinkingLevel.value;
  state.settings.showThinking = DOM.showThinking.checked;
  state.settings.autoScroll = DOM.autoScroll.checked;
  state.settings.showTimestamp = DOM.showTimestamp.checked;
  state.settings.markdownRender = DOM.markdownRender.checked;
  
  localStorage.setItem('memoryai_settings', JSON.stringify(state.settings));
  ui.closeSettings();
}

async function healthCheck() {
  DOM.healthStatus.textContent = '检查中...';
  DOM.healthStatus.className = 'health-status';
  
  try {
    const data = await api.healthCheck();
    const ok = data?.status === 'healthy';
    DOM.healthStatus.textContent = ok ? '✓ 运行正常' : '✗ 状态异常';
    DOM.healthStatus.className = `health-status ${ok ? 'ok' : 'error'}`;
  } catch (e) {
    DOM.healthStatus.textContent = `✗ ${e.message}`;
    DOM.healthStatus.className = 'health-status error';
  }
}

async function createNewSession() {
  try {
    ui.showLoading('创建会话...');
    const data = await api.createSession();
    state.currentSessionId = data.session_id;
    
    state.sessions.unshift({
      session_id: data.session_id,
      created_at: data.created_at,
    });
    
    ui.renderSessionsList();
    ui.updateSessionInfo();
    ui.clearMessages();
    ui.resetBadges();
    
    if (window.innerWidth <= 768) {
      DOM.sidebar.classList.remove('open');
    }
  } catch (e) {
    console.error('Failed to create session:', e);
    alert(`创建会话失败: ${e.message}`);
  } finally {
    ui.hideLoading();
  }
}

async function loadSession(sessionId) {
  try {
    ui.showLoading('加载会话...');
    state.currentSessionId = sessionId;
    
    const data = await api.getSessionMessages(sessionId);
    state.messages = data.messages || [];
    
    ui.clearMessages();
    state.messages.forEach(msg => {
      ui.addMessage(msg.role, msg.content, msg.timestamp);
    });
    
    await loadTopics();
    
    ui.updateSessionInfo();
    ui.renderSessionsList();
    ui.resetBadges();
    
    if (window.innerWidth <= 768) {
      DOM.sidebar.classList.remove('open');
    }
  } catch (e) {
    console.error('Failed to load session:', e);
    alert(`加载会话失败: ${e.message}`);
  } finally {
    ui.hideLoading();
  }
}

async function loadTopics() {
  if (!state.currentSessionId) {
    state.topics = [];
    ui.renderTopicsList();
    return;
  }
  
  try {
    const data = await api.getTopics(state.currentSessionId);
    state.topics = data.topics || [];
    ui.renderTopicsList();
  } catch (e) {
    console.error('Failed to load topics:', e);
    state.topics = [];
    ui.renderTopicsList();
  }
}

async function loadArtifact(threadId) {
  if (!state.currentSessionId) return;
  
  try {
    ui.showLoading('加载知识文档...');
    const data = await api.getTopicArtifact(state.currentSessionId, threadId);
    ui.renderArtifact(data);
    ui.openArtifact();
  } catch (e) {
    console.error('Failed to load artifact:', e);
    alert(`加载知识文档失败: ${e.message}`);
  } finally {
    ui.hideLoading();
  }
}

async function sendMessage() {
  const content = DOM.messageInput.value.trim();
  if (!content || state.isLoading) return;
  
  DOM.messageInput.value = '';
  DOM.sendBtn.disabled = true;
  adjustTextareaHeight();
  
  // Add user message
  ui.addMessage('user', content);
  
  // Show thinking badge and typing indicator
  ui.showThinkingBadge();
  ui.addTypingIndicator();
  
  try {
    const data = await api.sendMessage(content, state.currentSessionId);
    
    // Update session ID if new
    if (!state.currentSessionId) {
      state.currentSessionId = data.session_id;
      state.sessions.unshift({
        session_id: data.session_id,
        created_at: new Date().toISOString(),
      });
      ui.renderSessionsList();
      ui.updateSessionInfo();
    }
    
    // Remove typing indicator and hide thinking badge
    ui.removeTypingIndicator();
    ui.hideThinkingBadge();
    
    // Build thinking info (Gemini 3)
    const thinking = data.thinking ? {
      enabled: data.thinking.enabled,
      level: data.thinking.level,
      summary: data.thinking.summary || '',
      thoughts_tokens: data.thinking.thoughts_tokens || 0,
      output_tokens: data.thinking.output_tokens || 0,
      total_tokens: data.thinking.total_tokens || 0,
    } : null;
    
    // Add assistant message with thinking
    ui.addMessageWithThinking('assistant', data.response, thinking);
    
    // Update badges
    ui.updateBadges(data.archived, data.archive_topic, data.context_loads, thinking);
    
    // Reload topics if archived
    if (data.archived) {
      await loadTopics();
    }
  } catch (e) {
    ui.removeTypingIndicator();
    ui.hideThinkingBadge();
    ui.addMessage('assistant', `抱歉，发生了错误：${e.message}`);
    console.error('Failed to send message:', e);
  }
}

function adjustTextareaHeight() {
  const textarea = DOM.messageInput;
  textarea.style.height = 'auto';
  textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
}

// ============================================
// Event Listeners
// ============================================
function initEventListeners() {
  DOM.sidebarToggle.addEventListener('click', ui.toggleSidebar);
  DOM.newChatBtn.addEventListener('click', createNewSession);
  
  DOM.refreshSessionsBtn.addEventListener('click', () => {
    ui.renderSessionsList();
  });
  
  DOM.refreshTopicsBtn.addEventListener('click', loadTopics);
  
  DOM.settingsBtn.addEventListener('click', ui.openSettings);
  DOM.closeSettingsBtn.addEventListener('click', ui.closeSettings);
  DOM.panelOverlay.addEventListener('click', ui.closeSettings);
  DOM.saveSettingsBtn.addEventListener('click', saveSettings);
  DOM.healthCheckBtn.addEventListener('click', healthCheck);
  
  DOM.closeArtifactBtn.addEventListener('click', ui.closeArtifact);
  DOM.artifactOverlay.addEventListener('click', ui.closeArtifact);
  
  DOM.clearChatBtn.addEventListener('click', () => {
    if (confirm('确定要清空当前对话显示吗？')) {
      ui.clearMessages();
      ui.resetBadges();
    }
  });
  
  DOM.messageInput.addEventListener('input', () => {
    adjustTextareaHeight();
    DOM.sendBtn.disabled = !DOM.messageInput.value.trim();
  });
  
  DOM.messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  
  DOM.sendBtn.addEventListener('click', sendMessage);
  
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (DOM.settingsPanel.classList.contains('open')) {
        ui.closeSettings();
      }
      if (DOM.artifactPanel.classList.contains('open')) {
        ui.closeArtifact();
      }
    }
    
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      createNewSession();
    }
  });
  
  document.addEventListener('click', (e) => {
    if (window.innerWidth <= 768 && 
        DOM.sidebar.classList.contains('open') &&
        !DOM.sidebar.contains(e.target) &&
        !DOM.sidebarToggle.contains(e.target)) {
      DOM.sidebar.classList.remove('open');
    }
  });
}

// ============================================
// Initialize
// ============================================
function init() {
  loadSettings();
  initEventListeners();
  
  ui.updateSessionInfo();
  ui.renderSessionsList();
  ui.renderTopicsList();
  
  DOM.messageInput.focus();
  
  console.log('Memory AI initialized with Gemini 3 thinking support');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
