let currentUser = null;
let currentToken = localStorage.getItem('token');
let currentRole = localStorage.getItem('role');
let userName = localStorage.getItem('name');
let reminderTimeMins = 15;
let reminderCheckInterval = null;
let myTasksCache = [];
let chartInstances = {};

let timerTaskId = null;
let timerInterval = null;
let timerSeconds = 0;

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span>${message}</span>
        <button class="toast-close" onclick="this.parentElement.remove()">✕</button>
    `;
    container.appendChild(toast);
    setTimeout(() => {
        if(toast.parentElement) toast.remove();
    }, 5000);
}

function checkReminders() {
    if(!myTasksCache || myTasksCache.length === 0) return;
    const now = new Date();
    myTasksCache.forEach(task => {
        if(task.status !== 'completed' && task.due_date) {
            const dueDate = new Date(task.due_date);
            const diffMs = dueDate - now;
            const diffMins = Math.floor(diffMs / 60000);
            
            if(diffMins > 0 && diffMins <= reminderTimeMins && !task.notified) {
                showToast(`Reminder: ${task.title} is due in ${diffMins} minutes!`, 'warning');
                task.notified = true;
                apiCall('/reminders/send', 'POST', {title: task.title}).catch(e => console.log('Reminder email error:', e));
            }
        }
    });
}

async function apiCall(endpoint, method = 'GET', data = null) {
    const headers = { 'Content-Type': 'application/json' };
    if (currentToken) {
        headers['Authorization'] = `Bearer ${currentToken}`;
    }
    const options = { method, headers };
    if (data) {
        options.body = JSON.stringify(data);
    }
    try {
        const res = await fetch(`/api${endpoint}`, options);
        const json = await res.json();
        if (!res.ok) throw new Error(json.error || 'API Error');
        return json;
    } catch (err) {
        throw err;
    }
}

function showTab(tab) {
    document.getElementById('login-tab').style.display = tab === 'login' ? 'block' : 'none';
    document.getElementById('register-tab').style.display = tab === 'register' ? 'block' : 'none';
    const btns = document.querySelectorAll('.tab-btn');
    btns[0].className = tab === 'login' ? 'tab-btn active' : 'tab-btn';
    btns[1].className = tab === 'register' ? 'tab-btn active' : 'tab-btn';
}

function showPage(pageId) {
    document.querySelectorAll('.page').forEach(p => p.style.display = 'none');
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.getElementById(`page-${pageId}`).style.display = 'block';
    document.getElementById(`nav-${pageId}`).classList.add('active');
    
    if(pageId === 'dashboard') loadDashboard();
    if(pageId === 'tasks') loadTasks();
    if(pageId === 'schedule') loadSchedule();
    if(pageId === 'templates') loadTemplates();
    if(pageId === 'profile') loadProfile();
    if(pageId === 'admin') loadAdminStats();
}

async function login() {
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;
    const err = document.getElementById('login-error');
    try {
        const res = await apiCall('/login', 'POST', {email, password});
        setAuth(res);
    } catch(e) {
        err.innerText = e.message;
    }
}

async function register() {
    const name = document.getElementById('reg-name').value;
    const email = document.getElementById('reg-email').value;
    const password = document.getElementById('reg-password').value;
    const confirm = document.getElementById('reg-confirm').value;
    const err = document.getElementById('reg-error');
    if(password !== confirm) {
        err.innerText = "Passwords do not match";
        return;
    }
    try {
        const res = await apiCall('/register', 'POST', {name, email, password});
        setAuth(res);
    } catch(e) {
        err.innerText = e.message;
    }
}

function urlB64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/\-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}

function setAuth(data) {
    localStorage.setItem('token', data.token);
    localStorage.setItem('role', data.role);
    localStorage.setItem('name', data.name);
    currentToken = data.token;
    currentRole = data.role;
    userName = data.name;
    initApp();
}

function logout() {
    localStorage.clear();
    currentToken = null;
    currentRole = null;
    userName = null;
    if(reminderCheckInterval) clearInterval(reminderCheckInterval);
    document.getElementById('auth-section').style.display = 'flex';
    document.getElementById('app-section').style.display = 'none';
}

async function initApp() {
    if(!currentToken) {
        document.getElementById('auth-section').style.display = 'flex';
        document.getElementById('app-section').style.display = 'none';
        return;
    }
    document.getElementById('auth-section').style.display = 'none';
    document.getElementById('app-section').style.display = 'flex';
    document.getElementById('sidebar-user').innerText = `Hello, ${userName}`;
    document.getElementById('greeting').innerText = `Welcome back, ${userName}!`;
    document.getElementById('admin-link').style.display = currentRole === 'admin' ? 'block' : 'none';
    
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/static/sw.js').then(reg => {
            reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlB64ToUint8Array('BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuB-3qOX7jV-1i4lT4rKkAQrG4')
            }).then(sub => {
                apiCall('/push/subscribe', 'POST', sub).catch(e => console.log(e));
            }).catch(e => console.log('Push sub error:', e));
        }).catch(err => console.log('SW registration failed:', err));
    }
    
    try {
        const profile = await apiCall('/profile');
        reminderTimeMins = profile.reminder_time || 15;
    } catch(e) {}
    
    if(reminderCheckInterval) clearInterval(reminderCheckInterval);
    reminderCheckInterval = setInterval(checkReminders, 60000);
    
    showPage('dashboard');
}

async function loadDashboard() {
    try {
        const stats = await apiCall('/tasks/stats');
        document.getElementById('stats-grid').innerHTML = `
            <div class="stat-card">
                <div class="stat-title">Total Tasks</div>
                <div class="stat-value">${stats.total}</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">Completed</div>
                <div class="stat-value" style="color:var(--success)">${stats.completed}</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">Completion Rate</div>
                <div class="stat-value">${stats.completion_rate}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">Pending</div>
                <div class="stat-value" style="color:var(--warning)">${stats.pending}</div>
            </div>
        `;
        renderCharts(stats);
        const tasks = await apiCall('/tasks?status=pending');
        myTasksCache = tasks;
        const priorityTasks = tasks.filter(t => t.priority === 'high').slice(0, 5);
        let ptHtml = '';
        priorityTasks.forEach(t => {
            ptHtml += `
            <div class="task-card high" style="margin-bottom:10px;">
                <div class="task-info">
                    <h4>${t.title}</h4>
                    <div class="task-meta">Due: ${t.due_date ? t.due_date.replace('T', ' ') : 'None'}</div>
                </div>
            </div>`;
        });
        if(priorityTasks.length === 0) ptHtml = '<p>No high priority tasks pending!</p>';
        document.getElementById('priority-tasks-list').innerHTML = ptHtml;
    } catch(e) {
        showToast(e.message, 'error');
    }
}

function renderCharts(stats) {
    if(chartInstances.cat) chartInstances.cat.destroy();
    if(chartInstances.week) chartInstances.week.destroy();

    const ctxCat = document.getElementById('category-chart').getContext('2d');
    chartInstances.cat = new Chart(ctxCat, {
        type: 'bar',
        data: {
            labels: stats.by_category.map(c => c.category),
            datasets: [
                {
                    label: 'Tasks Count',
                    data: stats.by_category.map(c => c.count),
                    backgroundColor: '#4F46E5'
                },
                {
                    label: 'Time Spent (mins)',
                    data: stats.by_category.map(c => c.total_time || 0),
                    backgroundColor: '#10b981'
                }
            ]
        },
        options: { responsive: true, maintainAspectRatio: false }
    });

    if(chartInstances.pri) chartInstances.pri.destroy();
    const ctxPri = document.getElementById('priority-chart').getContext('2d');
    chartInstances.pri = new Chart(ctxPri, {
        type: 'doughnut',
        data: {
            labels: stats.by_priority.map(p => p.priority),
            datasets: [{
                data: stats.by_priority.map(p => p.count),
                backgroundColor: ['#f59e0b', '#10b981', '#ef4444']
            }]
        },
        options: { responsive: true, maintainAspectRatio: false }
    });

    const ctxWeek = document.getElementById('weekly-chart').getContext('2d');
    chartInstances.week = new Chart(ctxWeek, {
        type: 'line',
        data: {
            labels: stats.weekly_trends.map(w => w.d),
            datasets: [{
                label: 'Tasks Created',
                data: stats.weekly_trends.map(w => w.c),
                borderColor: '#4F46E5',
                tension: 0.4,
                fill: true,
                backgroundColor: 'rgba(79, 70, 229, 0.1)'
            }]
        },
        options: { responsive: true, maintainAspectRatio: false }
    });
}

async function loadTasks() {
    const p = document.getElementById('filter-priority').value;
    const c = document.getElementById('filter-category').value;
    const s = document.getElementById('filter-status').value;
    const q = document.getElementById('search-input').value;
    
    try {
        const tasks = await apiCall(`/tasks?priority=${p}&category=${c}&status=${s}&search=${q}`);
        myTasksCache = tasks;
        let html = '';
        tasks.forEach(t => {
            const badgeClass = t.status === 'completed' ? 'completed' : t.status === 'in-progress' ? 'in-progress' : 'pending';
            html += `
            <div class="task-card ${t.priority}">
                <div class="task-info">
                    <h4>${t.title} ${t.is_recurring ? '🔄' : ''}</h4>
                    <div class="task-meta">
                        <span class="badge ${badgeClass}">${t.status.toUpperCase()}</span>
                        <span>🗓️ ${t.due_date ? t.due_date.replace('T', ' ') : 'No date'}</span>
                        <span>⏱️ ${t.duration}m (Spent: ${t.time_spent || 0}m)</span>
                    </div>
                </div>
                <div class="task-actions">
                    <button class="btn-complete" onclick="updateTaskStatus(${t.id}, 'completed')" title="Mark Complete">✓</button>
                    <button class="btn-secondary" style="padding:4px 8px;" onclick="addTimePrompt(${t.id})" title="Track Time">+⏳</button>
                    <button class="btn-edit" onclick="editTask(${t.id})" title="Edit">✎</button>
                    <button class="btn-delete" onclick="deleteTaskPrompt(${t.id}, 'task')" title="Delete">✕</button>
                </div>
            </div>`;
        });
        document.getElementById('tasks-list').innerHTML = html || '<p>No tasks found.</p>';
    } catch(e) {
        showToast(e.message, 'error');
    }
}

function openTaskModal() {
    document.getElementById('task-modal-error').innerText = '';
    document.getElementById('task-edit-id').value = '';
    document.getElementById('task-title').value = '';
    document.getElementById('task-desc').value = '';
    document.getElementById('task-due').value = '';
    document.getElementById('task-status').value = 'pending';
    document.getElementById('modal-title').innerText = 'Add Task';
    document.getElementById('task-modal').style.display = 'flex';
}

function closeTaskModal() {
    document.getElementById('task-modal').style.display = 'none';
}

async function editTask(id) {
    const task = myTasksCache.find(t => t.id === id);
    if(!task) return;
    openTaskModal();
    document.getElementById('modal-title').innerText = 'Edit Task';
    document.getElementById('task-edit-id').value = task.id;
    document.getElementById('task-title').value = task.title;
    document.getElementById('task-desc').value = task.description || '';
    document.getElementById('task-priority').value = task.priority;
    document.getElementById('task-category').value = task.category;
    document.getElementById('task-due').value = task.due_date || '';
    document.getElementById('task-duration').value = task.duration;
    document.getElementById('task-status').value = task.status;
    document.getElementById('task-recurring').checked = task.is_recurring === 1;
}

async function saveTask() {
    const id = document.getElementById('task-edit-id').value;
    const data = {
        title: document.getElementById('task-title').value,
        description: document.getElementById('task-desc').value,
        priority: document.getElementById('task-priority').value,
        category: document.getElementById('task-category').value,
        due_date: document.getElementById('task-due').value,
        duration: parseInt(document.getElementById('task-duration').value),
        status: document.getElementById('task-status').value,
        is_recurring: document.getElementById('task-recurring').checked ? 1 : 0
    };
    
    try {
        if(id) {
            await apiCall(`/tasks/${id}`, 'PUT', data);
            showToast('Task updated!', 'success');
        } else {
            await apiCall('/tasks', 'POST', data);
            showToast('Task created!', 'success');
        }
        closeTaskModal();
        loadTasks();
    } catch(e) {
        document.getElementById('task-modal-error').innerText = e.message;
    }
}

async function updateTaskStatus(id, status) {
    try {
        await apiCall(`/tasks/${id}`, 'PUT', {status});
        showToast(`Task marked as ${status}`, 'success');
        loadTasks();
    } catch(e) {
        showToast(e.message, 'error');
    }
}

function addTimePrompt(id) {
    const task = myTasksCache.find(t => t.id === id);
    if(!task) return;
    timerTaskId = id;
    timerSeconds = 0;
    document.getElementById('timer-task-title').innerText = task.title;
    document.getElementById('timer-display').innerText = "00:00";
    document.getElementById('timer-start-btn').style.display = 'inline-block';
    document.getElementById('timer-stop-btn').style.display = 'none';
    document.getElementById('timer-modal').style.display = 'flex';
}

function updateTimerDisplay() {
    const m = Math.floor(timerSeconds / 60).toString().padStart(2, '0');
    const s = (timerSeconds % 60).toString().padStart(2, '0');
    document.getElementById('timer-display').innerText = `${m}:${s}`;
}

function startTimer() {
    document.getElementById('timer-start-btn').style.display = 'none';
    document.getElementById('timer-stop-btn').style.display = 'inline-block';
    if(timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(() => {
        timerSeconds++;
        updateTimerDisplay();
    }, 1000);
}

function stopTimer() {
    document.getElementById('timer-start-btn').style.display = 'inline-block';
    document.getElementById('timer-stop-btn').style.display = 'none';
    if(timerInterval) clearInterval(timerInterval);
}

async function closeTimerModal() {
    stopTimer();
    document.getElementById('timer-modal').style.display = 'none';
    const mins = Math.ceil(timerSeconds / 60);
    if(mins > 0) {
        try {
            await apiCall(`/tasks/${timerTaskId}/track`, 'PUT', {time_spent: mins});
            showToast(`Added ${mins} minutes to task!`, 'success');
            loadTasks();
            if (document.getElementById('nav-dashboard').classList.contains('active')) loadDashboard();
        } catch(e) {
            showToast(e.message, 'error');
        }
    }
    timerTaskId = null;
    timerSeconds = 0;
}

let deleteTarget = null;
let deleteType = null;
function deleteTaskPrompt(id, type) {
    deleteTarget = id;
    deleteType = type;
    document.getElementById('confirm-modal').style.display = 'flex';
}

function closeConfirm() {
    document.getElementById('confirm-modal').style.display = 'none';
    deleteTarget = null;
    deleteType = null;
}

document.getElementById('confirm-btn').addEventListener('click', async () => {
    if(!deleteTarget) return;
    try {
        if(deleteType === 'task') {
            await apiCall(`/tasks/${deleteTarget}`, 'DELETE');
            showToast('Task deleted', 'success');
            loadTasks();
        } else if(deleteType === 'template') {
            await apiCall(`/templates/${deleteTarget}`, 'DELETE');
            showToast('Template deleted', 'success');
            loadTemplates();
        }
        closeConfirm();
    } catch(e) {
        showToast(e.message, 'error');
    }
});

async function loadSchedule() {
    try {
        const schedule = await apiCall('/tasks/schedule');
        let html = '';
        schedule.forEach(t => {
            html += `
            <div class="task-card ${t.priority}">
                <div class="task-info">
                    <h4><span style="color:var(--primary);margin-right:10px;">#${t.suggested_order}</span> ${t.title}</h4>
                    <div class="task-meta">
                        <span class="badge" style="background:#e0e7ff;color:#4f46e5">${t.reason}</span>
                        <span>⏱️ ${t.duration}m</span>
                    </div>
                </div>
                <div class="task-actions">
                    <button class="btn-complete" onclick="updateTaskStatus(${t.id}, 'completed')">✓</button>
                </div>
            </div>`;
        });
        document.getElementById('schedule-list').innerHTML = html || '<p>No pending tasks to schedule.</p>';
        
        const recurring = await apiCall('/tasks/recurring_suggestions');
        let rHtml = '';
        recurring.forEach(r => {
            rHtml += `
            <div class="task-card" style="border-left-color:var(--secondary)">
                <div class="task-info">
                    <h4>${r.title}</h4>
                    <div class="task-meta">
                        <span>Done ${r.freq} times recently. Auto-add?</span>
                    </div>
                </div>
                <div class="task-actions">
                    <button class="btn-primary" onclick="addRecurringAsTask('${r.title}', '${r.category}', '${r.priority}', ${r.duration})">Add Task</button>
                </div>
            </div>`;
        });
        document.getElementById('recurring-list').innerHTML = rHtml || '<p>No recurring patterns detected yet.</p>';
        
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function addRecurringAsTask(title, category, priority, duration) {
    try {
        await apiCall('/tasks', 'POST', {title, category, priority, duration, is_recurring: 1});
        showToast('Recurring task added!', 'success');
        loadSchedule();
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function loadTemplates() {
    try {
        const templates = await apiCall('/templates');
        let html = '';
        templates.forEach(t => {
            html += `
            <div class="task-card">
                <div class="task-info">
                    <h4>${t.title} ${t.is_routine ? '🔁 (Routine)' : ''}</h4>
                    <div class="task-meta">
                        <span class="badge">${t.category.toUpperCase()}</span>
                        <span>⏱️ ${t.duration}m</span>
                    </div>
                </div>
                <div class="task-actions">
                    <button class="btn-primary" onclick="useTemplate(${t.id})">Use</button>
                    ${t.user_id ? `<button class="btn-delete" onclick="deleteTaskPrompt(${t.id}, 'template')">✕</button>` : ''}
                </div>
            </div>`;
        });
        document.getElementById('templates-list').innerHTML = html || '<p>No templates found.</p>';
    } catch(e) {
        showToast(e.message, 'error');
    }
}

function openTemplateModal() {
    document.getElementById('t-title').value = '';
    document.getElementById('t-desc').value = '';
    document.getElementById('t-duration').value = '30';
    document.getElementById('t-routine').checked = false;
    document.getElementById('template-modal').style.display = 'flex';
}

function closeTemplateModal() {
    document.getElementById('template-modal').style.display = 'none';
}

async function saveTemplate() {
    const data = {
        title: document.getElementById('t-title').value,
        description: document.getElementById('t-desc').value,
        priority: document.getElementById('t-priority').value,
        category: document.getElementById('t-category').value,
        duration: parseInt(document.getElementById('t-duration').value),
        is_routine: document.getElementById('t-routine').checked ? 1 : 0
    };
    try {
        await apiCall('/templates', 'POST', data);
        showToast('Template saved!', 'success');
        closeTemplateModal();
        loadTemplates();
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function useTemplate(tid) {
    try {
        const templates = await apiCall('/templates');
        const t = templates.find(x => x.id === tid);
        if(!t) return;
        await apiCall('/tasks', 'POST', {
            title: t.title,
            description: t.description,
            priority: t.priority,
            category: t.category,
            duration: t.duration,
            is_recurring: t.is_routine
        });
        showToast('Task created from template!', 'success');
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function loadProfile() {
    try {
        const p = await apiCall('/profile');
        document.getElementById('profile-avatar').innerText = p.name.charAt(0).toUpperCase();
        document.getElementById('prof-name').value = p.name;
        document.getElementById('prof-email').value = p.email;
        document.getElementById('prof-work-hours').value = p.work_hours;
        document.getElementById('prof-focus').value = p.focus_blocks;
        document.getElementById('prof-reminder-time').value = p.reminder_time;
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function updateProfile() {
    const data = {
        name: document.getElementById('prof-name').value,
        work_hours: document.getElementById('prof-work-hours').value,
        focus_blocks: document.getElementById('prof-focus').value,
        reminder_time: parseInt(document.getElementById('prof-reminder-time').value)
    };
    try {
        await apiCall('/profile', 'PUT', data);
        const msg = document.getElementById('profile-msg');
        msg.innerText = "Profile updated successfully!";
        msg.style.display = 'block';
        localStorage.setItem('name', data.name);
        userName = data.name;
        document.getElementById('sidebar-user').innerText = `Hello, ${userName}`;
        reminderTimeMins = data.reminder_time;
        setTimeout(() => msg.style.display = 'none', 3000);
    } catch(e) {
        showToast(e.message, 'error');
    }
}

function showAdminTab(tab) {
    ['overview', 'ml', 'users', 'tasks', 'logs'].forEach(t => {
        document.getElementById(`admin-tab-${t}`).style.display = 'none';
        document.getElementById(`atab-${t}`).classList.remove('active');
    });
    document.getElementById(`admin-tab-${tab}`).style.display = 'block';
    document.getElementById(`atab-${tab}`).classList.add('active');
    
    if(tab === 'overview') loadAdminStats();
    if(tab === 'users') loadAdminUsers();
    if(tab === 'tasks') loadAdminTasks();
}

async function loadAdminStats() {
    try {
        const stats = await apiCall('/admin/stats');
        document.getElementById('admin-stats-grid').innerHTML = `
            <div class="stat-card">
                <div class="stat-title">Total Users</div>
                <div class="stat-value">${stats.total_users}</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">Active Users</div>
                <div class="stat-value">${stats.active_users}</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">Total Tasks</div>
                <div class="stat-value">${stats.total_tasks}</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">Completed Tasks</div>
                <div class="stat-value" style="color:var(--success)">${stats.completed_tasks}</div>
            </div>
        `;
        
        let logsHtml = '<table><tr><th>Time</th><th>User</th><th>Action</th><th>Details</th></tr>';
        stats.recent_logs.forEach(l => {
            logsHtml += `<tr><td>${l.created_at}</td><td>${l.name||'System'}</td><td>${l.action}</td><td>${l.details}</td></tr>`;
        });
        logsHtml += '</table>';
        document.getElementById('logs-container').innerHTML = logsHtml;
        
        let mlHtml = '';
        stats.ml_configs.forEach(m => {
            mlHtml += `
            <div class="form-group">
                <label>${m.model_name.toUpperCase()} - ${m.param_name}</label>
                <input type="text" id="mlconf-${m.id}" value="${m.param_value}">
            </div>`;
        });
        document.getElementById('ml-configs-container').innerHTML = mlHtml;
        
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function saveMLConfigs() {
    try {
        const stats = await apiCall('/admin/stats');
        const data = {};
        stats.ml_configs.forEach(m => {
            const val = document.getElementById(`mlconf-${m.id}`).value;
            data[m.id] = val;
        });
        await apiCall('/admin/ml_configs', 'PUT', data);
        showToast('ML Configurations Updated Successfully', 'success');
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function retrainML() {
    try {
        const res = await apiCall('/admin/ml_retrain', 'POST');
        showToast(`Models retrained. Accuracy: ${res.accuracy}`, 'success');
        loadAdminStats();
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function loadAdminUsers() {
    try {
        const users = await apiCall('/admin/users');
        let html = '<table><tr><th>Name</th><th>Email</th><th>Role</th><th>Tasks</th><th>Status</th><th>Action</th></tr>';
        users.forEach(u => {
            html += `<tr>
                <td>${u.name}</td>
                <td>${u.email}</td>
                <td><span class="badge">${u.role}</span></td>
                <td>${u.task_count}</td>
                <td><span class="badge ${u.is_active ? 'completed' : 'pending'}">${u.is_active ? 'Active' : 'Disabled'}</span></td>
                <td>
                    <button class="btn-secondary" style="padding:4px 8px;font-size:12px;" onclick="toggleUser(${u.id})">Toggle</button>
                    ${u.role !== 'admin' ? `<button class="btn-danger" style="padding:4px 8px;font-size:12px;" onclick="deleteUser(${u.id})">Delete</button>` : ''}
                </td>
            </tr>`;
        });
        html += '</table>';
        document.getElementById('users-table-container').innerHTML = html;
        
        const filter = document.getElementById('admin-task-user-filter');
        const currentVal = filter.value;
        filter.innerHTML = '<option value="">All Users</option>' + users.map(u => `<option value="${u.id}">${u.name}</option>`).join('');
        filter.value = currentVal;
        
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function toggleUser(id) {
    try {
        await apiCall(`/admin/users/${id}/toggle`, 'PUT');
        loadAdminUsers();
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function deleteUser(id) {
    if(!confirm('Are you sure you want to delete this user and all their data?')) return;
    try {
        await apiCall(`/admin/users/${id}`, 'DELETE');
        loadAdminUsers();
    } catch(e) {
        showToast(e.message, 'error');
    }
}

async function loadAdminTasks() {
    const uid = document.getElementById('admin-task-user-filter').value;
    try {
        const tasks = await apiCall(`/admin/tasks?user_id=${uid}`);
        let html = '<table><tr><th>Task</th><th>User</th><th>Category</th><th>Status</th><th>Due</th></tr>';
        tasks.forEach(t => {
            const bc = t.status === 'completed' ? 'completed' : t.status === 'in-progress' ? 'in-progress' : 'pending';
            html += `<tr>
                <td><strong>${t.title}</strong></td>
                <td>${t.user_name}</td>
                <td>${t.category}</td>
                <td><span class="badge ${bc}">${t.status}</span></td>
                <td>${t.due_date ? t.due_date.replace('T', ' ') : '-'}</td>
            </tr>`;
        });
        html += '</table>';
        document.getElementById('admin-tasks-table-container').innerHTML = html;
    } catch(e) {
        showToast(e.message, 'error');
    }
}

initApp();
