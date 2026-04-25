import React, { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { FileText, FileCode, FileSpreadsheet, Presentation, Archive, Download, X, Eye, LogOut } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { atomDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { authFetch, getToken, clearAuth } from './auth'

export default function App({ currentUser, onLogout }) {
  const [conversations, setConversations] = useState([])
  const [activeChannelId, setActiveChannelId] = useState('')
  const [status, setStatus] = useState({ text: '连接中', ok: false })
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [pendingFile, setPendingFile] = useState(null)
  const [showProfile, setShowProfile] = useState(false)
  const [isWaiting, setIsWaiting] = useState(false)
  const [previewImage, setPreviewImage] = useState(null)
  const [previewFile, setPreviewFile] = useState(null)
  const [hasMore, setHasMore] = useState(true)
  const [notice, setNotice] = useState(null)
  const lastMessageIdRef = useRef(null)
  const [profileData, setProfileData] = useState({
    channel_name: '',
    user_name: '',
    user_avatar: '',
    ai_name: '',
    ai_avatar: ''
  })

  const socketRef = useRef(null)
  const messagesEndRef = useRef(null)
  const fileInputRef = useRef(null)
  const userAvatarRef = useRef(null)
  const aiAvatarRef = useRef(null)
  const activeChannelIdRef = useRef('')
  const isComponentMounted = useRef(true)
  const noticeTimerRef = useRef(null)
  const conversationsRefreshTimerRef = useRef(null)

  const activeConv = conversations.find(c => c.channel_id === activeChannelId)
  const isGroupChat = activeConv?.kind === 'group'

  useEffect(() => {
    activeChannelIdRef.current = activeChannelId
    setIsWaiting(false) // 切换对话时重置等待状态
  }, [activeChannelId])



  useEffect(() => {
    isComponentMounted.current = true
    fetchConversations()
    joinFromInviteUrl()
    connectWebSocket()
    return () => {
      isComponentMounted.current = false
      window.clearTimeout(noticeTimerRef.current)
      window.clearTimeout(conversationsRefreshTimerRef.current)
      if (socketRef.current) socketRef.current.close()
    }
  }, [])

  useEffect(() => {
    if (activeConv) {
      setProfileData({
        channel_name: activeConv.channel_name || '',
        user_name: activeConv.user_name || '',
        user_avatar: activeConv.user_avatar || '',
        ai_name: activeConv.ai_name || '',
        ai_avatar: activeConv.ai_avatar || ''
      })
    }
  }, [activeChannelId, conversations])

  useEffect(() => {
    setHasMore(true)
  }, [activeChannelId])

  useEffect(() => {
    const lastMsg = messages[messages.length - 1]
    if (lastMsg && lastMsg.id !== lastMessageIdRef.current) {
      lastMessageIdRef.current = lastMsg.id
      scrollToBottom()
    } else if (isWaiting) {
      scrollToBottom()
    }
  }, [messages, isWaiting])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const showNotice = (message, level = 'warning') => {
    setNotice({ message, level })
    window.clearTimeout(noticeTimerRef.current)
    noticeTimerRef.current = window.setTimeout(() => setNotice(null), 5000)
  }

  const isAiMentioned = (content) => {
    if (!isGroupChat) return true
    const names = new Set(['AI', 'NekroAgent'])
    if (activeConv?.ai_name) names.add(activeConv.ai_name)
    return Array.from(names).some(name => content.toLowerCase().includes(`@${name}`.toLowerCase()))
  }

  const handleScroll = async (e) => {
    const { scrollTop } = e.currentTarget
    if (scrollTop === 0 && messages.length > 0 && hasMore) {
      const container = e.currentTarget
      const previousHeight = container.scrollHeight
      const oldestMsg = messages.find(m => typeof m.id === 'number') || messages[0]
      if (!oldestMsg || !oldestMsg.id) return

      try {
        const res = await authFetch(`/api/conversations/${activeChannelId}/messages?before_id=${oldestMsg.id}&limit=50`)
        const data = await res.json()
        if (data.items && data.items.length > 0) {
          setMessages(prev => {
            const newItems = data.items.filter(item => !prev.some(p => p.id === item.id))
            return [...newItems, ...prev]
          })
          if (data.items.length < 50) {
            setHasMore(false)
          }
          setTimeout(() => {
            if (container) {
              container.scrollTop = container.scrollHeight - previousHeight
            }
          }, 0)
        } else {
          setHasMore(false)
        }
      } catch (err) {
        console.error('加载历史消息失败:', err)
      }
    }
  }

  const fetchConversations = async () => {
    try {
      const res = await authFetch('/api/conversations')
      const data = await res.json()
      const items = data.items || []
      setConversations(items)
      if (items.length && !activeChannelIdRef.current) {
        setActiveChannelId(items[0].channel_id)
      }
    } catch (err) {
      console.error('获取对话列表失败:', err)
    }
  }

  const joinFromInviteUrl = async () => {
    const match = window.location.pathname.match(/^\/invite\/([^/]+)$/)
    if (!match) return
    const inviteKey = decodeURIComponent(match[1])
    try {
      const res = await authFetch(`/api/invite/${encodeURIComponent(inviteKey)}/join`, { method: 'POST' })
      const item = await res.json()
      if (!res.ok) throw new Error(item.detail || '加入群聊失败')
      setConversations(prev => {
        if (prev.some(conv => conv.channel_id === item.channel_id)) {
          return prev.map(conv => conv.channel_id === item.channel_id ? item : conv)
        }
        return [item, ...prev]
      })
      selectConversation(item.channel_id)
      showNotice(`已加入「${item.channel_name}」`, 'success')
      window.history.replaceState({}, '', '/')
    } catch (err) {
      showNotice(err.message || '加入群聊失败', 'error')
    }
  }

  const scheduleFetchConversations = () => {
    window.clearTimeout(conversationsRefreshTimerRef.current)
    conversationsRefreshTimerRef.current = window.setTimeout(() => {
      if (isComponentMounted.current) fetchConversations()
    }, 250)
  }

  const connectWebSocket = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = window.location.host
    const token = getToken()
    const wsUrl = `${protocol}://${host}/ws?token=${encodeURIComponent(token)}`
    
    const ws = new WebSocket(wsUrl)
    socketRef.current = ws

    ws.onopen = () => {
      if (!isComponentMounted.current) {
        ws.close()
        return
      }
      setStatus({ text: '已连接', ok: true })
      if (activeChannelIdRef.current) {
        ws.send(JSON.stringify({ action: 'select', channel_id: activeChannelIdRef.current }))
      }
    }

    ws.onclose = () => {
      if (!isComponentMounted.current) return
      setStatus({ text: '已断开，重连中', ok: false })
      setTimeout(() => {
        if (isComponentMounted.current) connectWebSocket()
      }, 1200)
    }

    ws.onmessage = (event) => {
      if (!isComponentMounted.current) return
      const payload = JSON.parse(event.data)
      if (payload.type === 'message') {
        if (!activeChannelIdRef.current || payload.channel_id === activeChannelIdRef.current || !payload.channel_id) {
          setMessages(prev => {
            if (prev.some(m => m.id === payload.id)) return prev
            return [...prev, payload]
          })
          if (payload.role !== 'user') {
            setIsWaiting(false)
          }
        }
        scheduleFetchConversations()
      } else if (payload.type === 'history') {
        if (activeChannelIdRef.current && payload.channel_id && activeChannelIdRef.current !== payload.channel_id) {
          return
        }
        if (payload.channel_id) setActiveChannelId(payload.channel_id)
        setMessages(payload.items || [])
        setIsWaiting(false)
      } else if (payload.type === 'conversations') {
        const items = payload.items || []
        setConversations(items)
        if (items.length && !activeChannelIdRef.current) {
          setActiveChannelId(items[0].channel_id)
        }
      } else if (payload.type === 'status') {
        setStatus({ text: payload.connected ? '已连接' : '连接中', ok: payload.connected })
      } else if (payload.type === 'error') {
        showNotice(payload.message || '操作失败', 'error')
        setMessages(prev => [...prev, { role: 'system', content: payload.message }])
        setIsWaiting(false)
      } else if (payload.type === 'notification') {
        if (!payload.channel_id || payload.channel_id === activeChannelIdRef.current) {
          showNotice(payload.message || '操作被系统拦截', payload.level || 'warning')
        }
        setIsWaiting(false)
      }
    }
  }

  const selectConversation = (channelId) => {
    activeChannelIdRef.current = channelId
    setActiveChannelId(channelId)
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ action: 'select', channel_id: channelId }))
    }
  }

  const createNewChat = async () => {
    const name = prompt('对话名称', '新对话')
    if (name === null) return
    try {
      const res = await authFetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_name: name }),
      })
      const item = await res.json()
      setConversations(prev => [item, ...prev])
      selectConversation(item.channel_id)
    } catch (err) {
      showNotice(err.message || '创建对话失败', 'error')
      console.error('创建对话失败:', err)
    }
  }

  const createGroupChat = async () => {
    const name = prompt('群聊名称', '新群聊')
    if (name === null) return
    try {
      const res = await authFetch('/api/groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_name: name }),
      })
      const item = await res.json()
      if (!res.ok) throw new Error(item.detail || '创建群聊失败')
      setConversations(prev => [item, ...prev])
      selectConversation(item.channel_id)
      showNotice(`已创建群聊「${item.channel_name}」`, 'success')
    } catch (err) {
      showNotice(err.message || '创建群聊失败', 'error')
      console.error('创建群聊失败:', err)
    }
  }

  const copyInviteLink = async () => {
    if (!activeChannelId) return
    try {
      const res = await authFetch(`/api/conversations/${activeChannelId}/invite`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '获取邀请链接失败')
      const inviteUrl = `${window.location.origin}${data.invite_path}`
      await navigator.clipboard.writeText(inviteUrl)
      showNotice('群聊邀请链接已复制', 'success')
    } catch (err) {
      showNotice(err.message || '复制群聊邀请链接失败', 'error')
    }
  }

  const saveProfileSettings = async () => {
    if (!activeChannelId) return
    try {
      const res = await authFetch(`/api/conversations/${activeChannelId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profileData),
      })
      const updated = await res.json()
      setConversations(prev => prev.map(item => item.channel_id === updated.channel_id ? updated : item))
      setShowProfile(false)
    } catch (err) {
      console.error('保存设置失败:', err)
    }
  }

  const handleFileChange = (e) => {
    setPendingFile(e.target.files[0] || null)
  }

  const uploadFile = async (file) => {
    const formData = new FormData()
    formData.append('file_data', file)
    const res = await authFetch('/api/upload', { method: 'POST', body: formData })
    if (!res.ok) {
      let message = '上传失败'
      try {
        const data = await res.json()
        message = data.detail || message
      } catch (err) {}
      throw new Error(message)
    }
    return await res.json()
  }

  const handleAvatarUpload = async (e, type) => {
    const file = e.target.files[0]
    if (!file) return
    try {
      const data = await uploadFile(file)
      if (type === 'user') {
        setProfileData(prev => ({ ...prev, user_avatar: data.file_url }))
      } else {
        setProfileData(prev => ({ ...prev, ai_avatar: data.file_url }))
      }
    } catch (err) {
      showNotice(err.message || '上传头像失败', 'error')
      console.error('上传头像失败:', err)
    }
  }

  const handleSend = async (e) => {
    e.preventDefault()
    const content = input.trim()
    if ((!content && !pendingFile) || socketRef.current?.readyState !== WebSocket.OPEN || !activeChannelId) return

    try {
      const shouldWaitForAi = isAiMentioned(content)
      setIsWaiting(shouldWaitForAi)
      let fileInfo = null
      if (pendingFile) {
        fileInfo = await uploadFile(pendingFile)
      }

      socketRef.current.send(JSON.stringify({
        action: 'send',
        channel_id: activeChannelId,
        content,
        file: fileInfo,
      }))
      
      setInput('')
      setPendingFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
    } catch (err) {
      setIsWaiting(false)
      showNotice(err.message || '发送消息失败', 'error')
      console.error('发送消息失败:', err)
    }
  }

  const getFileIcon = (fileName) => {
    const ext = (fileName || '').split('.').pop().toLowerCase()
    if (['html', 'js', 'css', 'json', 'py', 'java', 'cpp', 'md', 'ts'].includes(ext)) return <FileCode size={20} />
    if (['txt', 'log'].includes(ext)) return <FileText size={20} />
    if (['doc', 'docx'].includes(ext)) return <FileText size={20} />
    if (['xls', 'xlsx', 'csv'].includes(ext)) return <FileSpreadsheet size={20} />
    if (['ppt', 'pptx'].includes(ext)) return <Presentation size={20} />
    if (ext === 'pdf') return <FileText size={20} />
    if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return <Archive size={20} />
    return <FileText size={20} />
  }
  const getFileClass = (fileName) => {
    const ext = (fileName || '').split('.').pop().toLowerCase()
    if (['html', 'js', 'css', 'json', 'py', 'java', 'cpp', 'md', 'ts'].includes(ext)) return 'icon-code'
    if (['txt', 'log'].includes(ext)) return 'icon-txt'
    if (['doc', 'docx'].includes(ext)) return 'icon-doc'
    if (['xls', 'xlsx', 'csv'].includes(ext)) return 'icon-xls'
    if (['ppt', 'pptx'].includes(ext)) return 'icon-ppt'
    if (ext === 'pdf') return 'icon-pdf'
    if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 'icon-zip'
    return ''
  }
  const getFileSubtitle = (fileName, mimeType) => {
    const ext = (fileName || '').split('.').pop().toLowerCase()
    if (['txt', 'log'].includes(ext)) return 'Text · 文本文件'
    if (['doc', 'docx'].includes(ext)) return 'Word · 文档'
    if (['xls', 'xlsx', 'csv'].includes(ext)) return 'Excel · 表格'
    if (['ppt', 'pptx'].includes(ext)) return 'PowerPoint · 演示文稿'
    if (ext === 'pdf') return 'PDF · 便携式文档'
    if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 'Archive · 压缩包'
    if (['html', 'js', 'css', 'json', 'py', 'java', 'cpp', 'md', 'ts'].includes(ext)) return 'Code · 源文件'
    return mimeType || '未知类型'
  }

  const isTextFile = (name) => {
    if (!name) return false
    const ext = name.split('.').pop().toLowerCase()
    return ['md', 'html', 'txt', 'json', 'js', 'ts', 'jsx', 'css', 'py', 'yaml', 'yml', 'xml', 'log'].includes(ext)
  }

  const handlePreviewFile = async (name, url) => {
    try {
      const res = await fetch(url)
      if (!res.ok) {
        if (res.status === 404) {
          alert('文件已过期或被系统自动清理')
        } else {
          alert('无法加载文件内容')
        }
        return
      }
      const content = await res.text()
      const ext = name.split('.').pop().toLowerCase()
      let type = 'text'
      if (ext === 'md') type = 'md'
      else if (ext === 'html') type = 'html'
      setPreviewFile({ name, url, content, type })
    } catch (err) {
      console.error('预览文件失败:', err)
      alert('无法加载文件内容')
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="profile">
          <div className="avatar">
            <img src="/static/webchat.png" alt="Logo" />
          </div>
          <div className="profile-info">
            <div className="name">Nekro WebChat</div>
            <div className={`status ${status.ok ? 'online' : 'offline'}`}>{status.text}</div>
          </div>
        </div>
        
        <button className="new-chat" type="button" onClick={createNewChat}>
          <span className="plus-icon">＋</span> 新建对话
        </button>
        <button className="new-chat secondary" type="button" onClick={createGroupChat}>
          <span className="plus-icon">＋</span> 创建群聊
        </button>
        
        <div className="chat-list">
          {conversations.map(item => (
            <button
              key={item.channel_id}
              className={`chat-item ${item.channel_id === activeChannelId ? 'active' : ''}`}
              onClick={() => selectConversation(item.channel_id)}
            >
              <div className="chat-avatar">
                <img src={item.ai_avatar || '/static/ai.png'} alt={item.ai_name} />
              </div>
              <div className="chat-meta">
                <span className="chat-title">{item.channel_name}</span>
                <span className="chat-subtitle">{item.last_message || item.channel_id}</span>
              </div>
            </button>
          ))}
        </div>

        <div className="sidebar-footer">
          <div className="sidebar-user-info">
            <span className="sidebar-username">{currentUser?.display_name || currentUser?.username}</span>
          </div>
          <button className="logout-button" type="button" onClick={() => { clearAuth(); onLogout() }}>
            <LogOut size={16} />
            <span>退出</span>
          </button>
        </div>
      </aside>

      <section className="chat">
        {notice && (
          <div className={`toast ${notice.level || 'warning'}`} role="status">
            {notice.message}
          </div>
        )}
        <header className="chat-header">
          <div>
            <h1>{activeConv?.channel_name || 'WebChat'}</h1>
            <p>{activeConv ? `${activeConv.ai_name || 'NekroAgent'} · ${activeConv.channel_id}` : '-'}</p>
          </div>
          <button 
            className={`ghost-button ${showProfile ? 'active' : ''}`} 
            type="button" 
            onClick={() => setShowProfile(!showProfile)}
          >
            {isGroupChat ? '群聊设置' : '对话设置'}
          </button>
        </header>

        {showProfile && (
          <section className="profile-panel">
            <div className="form-group">
              <label>{isGroupChat ? '群聊名称' : '对话名称'}</label>
              <input 
                value={profileData.channel_name} 
                onChange={e => setProfileData(prev => ({ ...prev, channel_name: e.target.value }))} 
              />
            </div>
            <div className="form-group">
              <label>用户名</label>
              <input 
                value={profileData.user_name} 
                onChange={e => setProfileData(prev => ({ ...prev, user_name: e.target.value }))} 
              />
            </div>
            <div className="form-group">
              <label>用户头像</label>
              <div className="avatar-upload-group">
                <input 
                  type="text"
                  placeholder="头像 URL"
                  value={profileData.user_avatar} 
                  onChange={e => setProfileData(prev => ({ ...prev, user_avatar: e.target.value }))} 
                />
                <button type="button" className="upload-btn" onClick={() => userAvatarRef.current.click()}>上传</button>
                <input type="file" ref={userAvatarRef} hidden accept="image/*" onChange={(e) => handleAvatarUpload(e, 'user')} />
              </div>
            </div>
            <div className="form-group">
              <label>AI 名字</label>
              <input 
                value={profileData.ai_name} 
                onChange={e => setProfileData(prev => ({ ...prev, ai_name: e.target.value }))} 
              />
            </div>
            <div className="form-group">
              <label>AI 头像</label>
              <div className="avatar-upload-group">
                <input 
                  type="text"
                  placeholder="头像 URL"
                  value={profileData.ai_avatar} 
                  onChange={e => setProfileData(prev => ({ ...prev, ai_avatar: e.target.value }))} 
                />
                <button type="button" className="upload-btn" onClick={() => aiAvatarRef.current.click()}>上传</button>
                <input type="file" ref={aiAvatarRef} hidden accept="image/*" onChange={(e) => handleAvatarUpload(e, 'ai')} />
              </div>
            </div>
            <button className="save-button" type="button" onClick={saveProfileSettings}>
              保存设置
            </button>
            {isGroupChat && (
              <button className="invite-button" type="button" onClick={copyInviteLink}>
                复制群聊邀请链接
              </button>
            )}
          </section>
        )}

        <div className="messages" onScroll={handleScroll}>
          {messages.map((msg, index) => {
            const isOwn = msg.role === 'user' && String(msg.sender_id) === String(currentUser?.id)
            const rowRole = isOwn ? 'user' : (msg.role === 'user' ? 'other-user' : msg.role)
            const isSystem = msg.role === 'system'
            const avatarUrl = isOwn
              ? (currentUser?.avatar || activeConv?.user_avatar || '/static/user.png')
              : (msg.role === 'user' ? '/static/user.png' : (activeConv?.ai_avatar || '/static/ai.png'))
            const isImageOnly = msg.file_url && (msg.mime_type || '').startsWith('image/') && 
              (!msg.content || msg.content.trim() === `[图片] ${msg.file_name}` || msg.content.trim() === `[图片]${msg.file_name}`)
            
            return (
              <div key={index} className={`bubble-row ${rowRole}`}>
                {!isSystem && (
                  <div className="msg-avatar">
                    <img src={avatarUrl} alt={msg.sender_name} />
                  </div>
                )}
                <div className={`bubble ${isImageOnly ? 'bubble-image-only' : ''}`}>
                  {!isOwn && !isSystem && msg.role === 'user' && (
                    <div className="sender-name">{msg.sender_name || '用户'}</div>
                  )}
                  {msg.content && (!msg.file_url || (
                    msg.content.trim() !== `[文件] ${msg.file_name}` && 
                    msg.content.trim() !== `[文件]${msg.file_name}` &&
                    msg.content.trim() !== `[图片] ${msg.file_name}` && 
                    msg.content.trim() !== `[图片]${msg.file_name}`
                  )) && (
                     <div className="markdown-body">
                       <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                     </div>
                  )}
                  {msg.file_url && (
                    <div className="message-attachment">
                      {(msg.mime_type || '').startsWith('image/') ? (
                        <img 
                          className="bubble-image" 
                          src={msg.file_url} 
                          alt={msg.file_name || 'image'} 
                          onClick={(e) => {
                            if (!e.target.classList.contains('expired')) {
                              setPreviewImage(msg.file_url)
                            }
                          }}
                          onError={(e) => {
                            e.target.onerror = null;
                            e.target.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 300 100'%3E%3Crect width='300' height='100' fill='%23f5f5f5'/%3E%3Ctext x='150' y='50' font-family='sans-serif' font-size='14' fill='%23999' text-anchor='middle' alignment-baseline='middle'%3E图片已过期或被清理%3C/text%3E%3C/svg%3E";
                            e.target.classList.add('expired');
                            e.target.style.cursor = 'default';
                          }}
                          style={{ cursor: 'zoom-in' }}
                        />
                      ) : (
                        <div 
                          className={`file-attachment-card ${isTextFile(msg.file_name) ? 'clickable' : ''}`}
                          onClick={() => isTextFile(msg.file_name) && handlePreviewFile(msg.file_name, msg.file_url)}
                        >
                          <div className={`file-icon ${getFileClass(msg.file_name)}`}>{getFileIcon(msg.file_name)}</div>
                          <div className="file-meta">
                            <span className="file-name">{msg.file_name || '文件'}</span>
                            <span className="file-size">{getFileSubtitle(msg.file_name, msg.mime_type)}</span>
                          </div>

                          <a className="file-download-btn" href={msg.file_url} download={msg.file_name} target="_blank" rel="noreferrer" onClick={async (e) => {
                            e.stopPropagation();
                            try {
                              const res = await fetch(msg.file_url, { method: 'HEAD' });
                              if (!res.ok && res.status === 404) {
                                e.preventDefault();
                                alert('文件已过期或被系统自动清理');
                              }
                            } catch (err) {}
                          }}>
                             <Download size={18} />
                          </a>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )
          })}

          {isWaiting && (
            <div className="bubble-row assistant typing-indicator-row">
              <div className="msg-avatar">
                <img src={activeConv?.ai_avatar || '/static/ai.png'} alt="AI" />
              </div>
              <div className="bubble typing-indicator">
                <span></span>
                <span></span>
                <span></span>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        <form className="composer" onSubmit={handleSend}>
          <button 
            type="button" 
            className="icon-button attach-button" 
            onClick={() => fileInputRef.current?.click()}
          >
            ＋
          </button>
          <input 
            type="file" 
            ref={fileInputRef} 
            hidden 
            onChange={handleFileChange} 
          />
          
          <div className="compose-main">
            {pendingFile && (
              <div className="attachment-preview">
                已选择：{pendingFile.name}
                <button type="button" className="clear-file" onClick={() => setPendingFile(null)}>×</button>
              </div>
            )}
            <textarea 
              value={input} 
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleSend(e)
                }
              }}
              placeholder="输入消息，Enter 发送"
              rows="1"
            />
          </div>
          
          <button className="send-button" type="submit">
            发送
          </button>
        </form>
      </section>
      {previewImage && createPortal(
        <div className="image-preview-overlay" onClick={() => setPreviewImage(null)}>
          <button className="close-preview" onClick={() => setPreviewImage(null)}><X size={24} /></button>
          <img src={previewImage} alt="Preview" onClick={(e) => e.stopPropagation()} />
        </div>,
        document.body
      )}
      {previewFile && createPortal(
        <div className="file-preview-overlay" onClick={() => setPreviewFile(null)}>
          <div className="file-preview-modal" onClick={(e) => e.stopPropagation()}>
            <div className="file-preview-header">
              <span className="file-preview-title">{previewFile.name}</span>
              <button className="close-preview-btn" onClick={() => setPreviewFile(null)}><X size={24} /></button>
            </div>
            <div className="file-preview-body">
              {previewFile.type === 'md' && (
                <div className="markdown-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{previewFile.content}</ReactMarkdown>
                </div>
              )}
              {previewFile.type === 'html' && (
                <iframe 
                  srcDoc={previewFile.content} 
                  title={previewFile.name} 
                  style={{ width: '100%', height: '100%', minHeight: '500px', border: 'none', background: 'white' }}
                />
              )}
              {previewFile.type === 'text' && (
                <SyntaxHighlighter 
                  language={previewFile.name.split('.').pop().toLowerCase()} 
                  style={atomDark}
                  customStyle={{ margin: 0, borderRadius: 'var(--radius-md)', padding: '16px' }}
                >
                  {previewFile.content}
                </SyntaxHighlighter>
              )}
            </div>
          </div>
        </div>,
        document.body
      )}
    </main>
  )
}
