import React, { useState, useEffect, useRef } from 'react'

export default function App() {
  const [conversations, setConversations] = useState([])
  const [activeChannelId, setActiveChannelId] = useState('')
  const [status, setStatus] = useState({ text: '连接中', ok: false })
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [pendingFile, setPendingFile] = useState(null)
  const [showProfile, setShowProfile] = useState(false)
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

  const activeConv = conversations.find(c => c.channel_id === activeChannelId)

  useEffect(() => {
    fetchConversations()
    connectWebSocket()
    return () => {
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
    scrollToBottom()
  }, [messages])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const fetchConversations = async () => {
    try {
      const res = await fetch('/api/conversations')
      const data = await res.json()
      const items = data.items || []
      setConversations(items)
      if (items.length && !activeChannelId) {
        setActiveChannelId(items[0].channel_id)
      }
    } catch (err) {
      console.error('获取对话列表失败:', err)
    }
  }

  const connectWebSocket = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = window.location.host
    const wsUrl = `${protocol}://${host}/ws`
    
    const ws = new WebSocket(wsUrl)
    socketRef.current = ws

    ws.onopen = () => setStatus({ text: '已连接', ok: true })
    ws.onclose = () => {
      setStatus({ text: '已断开，重连中', ok: false })
      setTimeout(connectWebSocket, 1200)
    }

    ws.onmessage = (event) => {
      const payload = JSON.parse(event.data)
      if (payload.type === 'message') {
        if (!activeChannelId || payload.channel_id === activeChannelId || !payload.channel_id) {
          setMessages(prev => [...prev, payload])
        }
        fetchConversations()
      } else if (payload.type === 'history') {
        if (payload.channel_id) setActiveChannelId(payload.channel_id)
        setMessages(payload.items || [])
        fetchConversations()
      } else if (payload.type === 'conversations') {
        const items = payload.items || []
        setConversations(items)
        if (items.length && !activeChannelId) {
          setActiveChannelId(items[0].channel_id)
        }
      } else if (payload.type === 'status') {
        setStatus({ text: payload.connected ? '已连接' : '连接中', ok: payload.connected })
      } else if (payload.type === 'error') {
        setMessages(prev => [...prev, { role: 'system', content: payload.message }])
      }
    }
  }

  const selectConversation = (channelId) => {
    setActiveChannelId(channelId)
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ action: 'select', channel_id: channelId }))
    }
  }

  const createNewChat = async () => {
    const name = prompt('对话名称', '新对话')
    if (name === null) return
    try {
      const res = await fetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_name: name }),
      })
      const item = await res.json()
      setConversations(prev => [item, ...prev])
      selectConversation(item.channel_id)
    } catch (err) {
      console.error('创建对话失败:', err)
    }
  }

  const saveProfileSettings = async () => {
    if (!activeChannelId) return
    try {
      const res = await fetch(`/api/conversations/${activeChannelId}`, {
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
    const res = await fetch('/api/upload', { method: 'POST', body: formData })
    if (!res.ok) throw new Error('上传失败')
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
      console.error('上传头像失败:', err)
    }
  }

  const handleSend = async (e) => {
    e.preventDefault()
    const content = input.trim()
    if ((!content && !pendingFile) || socketRef.current?.readyState !== WebSocket.OPEN || !activeChannelId) return

    try {
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
      console.error('发送消息失败:', err)
    }
  }

  const getInitials = (name) => {
    return (name || '?').trim().slice(0, 2).toUpperCase()
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
        
        <div className="chat-list">
          {conversations.map(item => (
            <button
              key={item.channel_id}
              className={`chat-item ${item.channel_id === activeChannelId ? 'active' : ''}`}
              onClick={() => selectConversation(item.channel_id)}
            >
              <div className="chat-avatar">
                <img src={item.ai_avatar || '/static/webchat.png'} alt={item.ai_name} />
              </div>
              <div className="chat-meta">
                <span className="chat-title">{item.channel_name}</span>
                <span className="chat-subtitle">{item.channel_id}</span>
              </div>
            </button>
          ))}
        </div>
      </aside>

      <section className="chat">
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
            资料设置
          </button>
        </header>

        {showProfile && (
          <section className="profile-panel">
            <div className="form-group">
              <label>对话名称</label>
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
          </section>
        )}

        <div className="messages">
          {messages.map((msg, index) => {
            const isUser = msg.role === 'user'
            const isSystem = msg.role === 'system'
            const avatarUrl = isUser ? activeConv?.user_avatar : (activeConv?.ai_avatar || '/static/webchat.png')
            
            return (
              <div key={index} className={`bubble-row ${msg.role}`}>
                {!isSystem && (
                  <div className="msg-avatar">
                    {avatarUrl ? (
                      <img src={avatarUrl} alt={msg.sender_name} />
                    ) : (
                      getInitials(msg.sender_name)
                    )}
                  </div>
                )}
                <div className="bubble">
                  {msg.content && <div>{msg.content}</div>}
                  {msg.file_url && (
                    <div className="message-attachment">
                      {(msg.mime_type || '').startsWith('image/') ? (
                        <img className="bubble-image" src={msg.file_url} alt={msg.file_name || 'image'} />
                      ) : (
                        <a className="file-card" href={msg.file_url} target="_blank" rel="noreferrer">
                          📎 {msg.file_name || '文件'}
                        </a>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
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
    </main>
  )
}
