/* ─── Vault: Frontend JavaScript ─── */

const Vault = {
    // ── API helpers ─────────────────────────────────────────────────────
    async api(url, options = {}) {
        const defaults = {
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
        };
        const opts = { ...defaults, ...options };
        if (opts.body && typeof opts.body === 'object' && !(opts.body instanceof FormData)) {
            opts.body = JSON.stringify(opts.body);
        }
        if (opts.body instanceof FormData) {
            delete opts.headers['Content-Type'];
        }
        const resp = await fetch(url, opts);
        if (resp.status === 401) {
            window.location.href = '/login';
            return;
        }
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || `HTTP ${resp.status}`);
        }
        return data;
    },

    // ── Toast notifications ─────────────────────────────────────────────
    toast(message, type = 'info') {
        let container = document.querySelector('.toast-container');
        if (!container) {
            container = document.createElement('div');
            container.className = 'toast-container';
            document.body.appendChild(container);
        }
        const toast = document.createElement('div');
        toast.className = `alert alert-${type} mb-2`;
        toast.style.minWidth = '280px';
        toast.style.boxShadow = '0 4px 12px rgba(85,75,106,0.15)';
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; }, 3000);
        setTimeout(() => toast.remove(), 3500);
    },

    toastPublic(message) {
        let container = document.querySelector('.toast-container');
        if (!container) {
            container = document.createElement('div');
            container.className = 'toast-container';
            document.body.appendChild(container);
        }
        const toast = document.createElement('div');
        toast.className = `alert toast-public mb-2`;
        toast.style.minWidth = '280px';
        toast.style.boxShadow = '0 6px 20px rgba(0,0,0,0.25)';
        toast.style.opacity = '1';
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; }, 3000);
        setTimeout(() => toast.remove(), 3500);
    },

    // ── Format helpers ──────────────────────────────────────────────────
    formatSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    },

    formatDate(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        const now = new Date();
        const diff = (now - d) / 1000;
        if (diff < 60) return 'just now';
        if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined });
    },

    statusBadge(status) {
        return `<span class="badge badge-status-${status}">${status.replace('_', ' ')}</span>`;
    },

    actionBadge(action) {
        return `<span class="badge badge-action-${action}">${action}</span>`;
    },

    roleBadge(role) {
        return `<span class="badge badge-role-${role}">${role}</span>`;
    },

    // ── File icon ───────────────────────────────────────────────────────
    fileIcon(name, isFolder) {
        if (isFolder) {
            return '<svg width="16" height="16" viewBox="0 0 16 16" fill="#8a7a96"><path d="M1.75 1A1.75 1.75 0 000 2.75v10.5C0 14.216.784 15 1.75 15h12.5A1.75 1.75 0 0016 13.25v-8.5A1.75 1.75 0 0014.25 3H7.5a.25.25 0 01-.2-.1l-.9-1.2C6.07 1.26 5.55 1 5 1H1.75z"/></svg>';
        }
        return '<svg width="16" height="16" viewBox="0 0 16 16" fill="#8a7a96"><path d="M3.75 1.5a.25.25 0 00-.25.25v12.5c0 .138.112.25.25.25h8.5a.25.25 0 00.25-.25V4.664a.25.25 0 00-.073-.177l-2.914-2.914a.25.25 0 00-.177-.073H3.75zM2 1.75C2 .784 2.784 0 3.75 0h6.586c.464 0 .909.184 1.237.513l2.914 2.914c.329.328.513.773.513 1.237v9.586A1.75 1.75 0 0113.25 16h-9.5A1.75 1.75 0 012 14.25V1.75z"/></svg>';
    },

    // ── Breadcrumb builder ──────────────────────────────────────────────
    buildBreadcrumb(path) {
        const parts = path.split('/').filter(Boolean);
        let html = '<a href="/browse">root</a>';
        let accumulated = '';
        for (const part of parts) {
            accumulated += (accumulated ? '/' : '') + part;
            html += ` <span class="separator">/</span> <a href="/browse/${accumulated}">${part}</a>`;
        }
        return html;
    },

    // ── Confirm dialog ──────────────────────────────────────────────────
    async confirm(title, message) {
        return new Promise(resolve => {
            const modal = document.getElementById('confirmModal');
            if (!modal) { resolve(window.confirm(message)); return; }
            modal.querySelector('.modal-title').textContent = title;
            modal.querySelector('.modal-body p').textContent = message;
            const bsModal = new bootstrap.Modal(modal);
            const confirmBtn = modal.querySelector('.btn-confirm');
            const handler = () => { resolve(true); bsModal.hide(); confirmBtn.removeEventListener('click', handler); };
            confirmBtn.addEventListener('click', handler);
            modal.addEventListener('hidden.bs.modal', () => { resolve(false); confirmBtn.removeEventListener('click', handler); }, { once: true });
            bsModal.show();
        });
    },

    // ── CodeMirror mode detection ───────────────────────────────────────
    getEditorMode(path) {
        const ext = path.split('.').pop().toLowerCase();
        const map = {
            js: 'javascript', jsx: 'jsx', ts: 'javascript', tsx: 'jsx',
            py: 'python', rb: 'ruby', go: 'go', rs: 'rust',
            java: 'text/x-java', c: 'text/x-csrc', cpp: 'text/x-c++src',
            h: 'text/x-csrc', cs: 'text/x-csharp',
            html: 'htmlmixed', htm: 'htmlmixed', xml: 'xml', svg: 'xml',
            css: 'css', scss: 'text/x-scss', less: 'text/x-less',
            json: 'application/json', yaml: 'yaml', yml: 'yaml',
            md: 'markdown', sql: 'sql', sh: 'shell', bash: 'shell',
            dockerfile: 'dockerfile', toml: 'toml',
            php: 'php', swift: 'swift', kt: 'text/x-kotlin',
        };
        return map[ext] || 'text/plain';
    },
};


/* ─── Page-specific initializers ─── */

// File browser
async function initBrowser(currentPath) {
    const container = document.getElementById('file-list');
    if (!container) return;

    container.innerHTML = '<div class="loading-spinner"><div class="spinner-border spinner-border-sm me-2"></div> Loading...</div>';

    try {
        const data = await Vault.api(`/api/files/browse?path=${encodeURIComponent(currentPath)}`);

        if (data.items.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <svg width="48" height="48" viewBox="0 0 16 16" fill="#8a7a96"><path d="M3.75 1.5a.25.25 0 00-.25.25v12.5c0 .138.112.25.25.25h8.5a.25.25 0 00.25-.25V4.664a.25.25 0 00-.073-.177l-2.914-2.914a.25.25 0 00-.177-.073H3.75z"/></svg>
                    <p>This folder is empty</p>
                    <a href="/new?path=${encodeURIComponent(currentPath ? currentPath + '/' : '')}" class="btn btn-sm btn-primary mt-3">Create a file</a>
                </div>`;
            return;
        }

        let html = '';
        // Parent directory link
        if (currentPath) {
            const parent = currentPath.split('/').slice(0, -1).join('/');
            html += `<a class="file-item" href="/browse/${parent}">
                <span class="icon">..</span>
                <span class="name">..</span>
            </a>`;
        }

        for (const item of data.items) {
            const href = item.is_folder ? `/browse/${item.path}` : `/edit?path=${encodeURIComponent(item.path)}`;
            const authorIdAttr = item.author_id ? ` data-author-id="${item.author_id}"` : '';
            html += `<a class="file-item" href="${href}" data-path="${item.path}" data-folder="${item.is_folder}"${authorIdAttr}>
                <span class="icon">${Vault.fileIcon(item.name, item.is_folder)}</span>
                <span class="name">${item.name}</span>
                <span class="meta">${item.is_folder ? '' : Vault.formatSize(item.size)}</span>
            </a>`;
        }
        container.innerHTML = html;

        // Right-click context menu
        container.querySelectorAll('.file-item').forEach(el => {
            el.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                showContextMenu(e, el.dataset.path, el.dataset.folder === 'true', el.dataset.authorId);
            });
        });
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger m-3">${err.message}</div>`;
    }
}

function showContextMenu(event, path, isFolder, authorId) {
    // Remove existing menu
    document.querySelectorAll('.context-menu').forEach(m => m.remove());

    const menu = document.createElement('div');
    menu.className = 'context-menu dropdown-menu show';
    menu.style.cssText = `position:fixed;left:${event.clientX}px;top:${event.clientY}px;z-index:9999;`;

    let items = '';
    const userRole = document.body.dataset.userRole || '';
    const currentUserId = document.body.dataset.userId || '';
    const canWrite = ['admin','approver','editor'].includes(userRole);
    const isAuthor = authorId && String(authorId) === String(currentUserId);

    if (isFolder) {
        items = `
            <a class="dropdown-item" href="/new?path=${encodeURIComponent(path + '/')}">New file here</a>`;
        if (canWrite) {
            items += `\n            <a class="dropdown-item text-danger" onclick="deleteFolder('${path}')">Delete folder</a>`;
        }
    } else {
        items = `
            <a class="dropdown-item" href="/edit?path=${encodeURIComponent(path)}">Edit</a>
            <a class="dropdown-item" href="/history?path=${encodeURIComponent(path)}">History</a>
            <div class="dropdown-divider"></div>
            <a class="dropdown-item" onclick="togglePublic('${path}')">Toggle public link</a>
            <a class="dropdown-item" onclick="copyBucketUrl('${path}')">Copy bucket URL</a>
            <a class="dropdown-item" onclick="toggleArchive('${path}')">Toggle archive</a>`;
        if (canWrite || isAuthor) {
            items += `\n            <div class="dropdown-divider"></div>\n            <a class="dropdown-item text-danger" onclick="deleteFile('${path}')">Delete</a>`;
        }
    }
    menu.innerHTML = items;
    document.body.appendChild(menu);

    const close = () => { menu.remove(); document.removeEventListener('click', close); };
    setTimeout(() => document.addEventListener('click', close), 10);
}

async function deleteFile(path) {
    if (!await Vault.confirm('Delete File', `Stage deletion of "${path}"? This will be added to your draft Change Request for approval.`)) return;
    try {
        const result = await Vault.api(`/api/files/delete?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
        Vault.toast(`Deletion staged in CR #${result.cr_id}`, 'success');
    } catch (err) {
        Vault.toast(err.message, 'danger');
    }
}

async function deleteFolder(path) {
    if (!await Vault.confirm('Delete Folder', `Stage deletion of "${path}" and all its files? This will be added to your draft CR for approval.`)) return;
    try {
        const result = await Vault.api(`/api/files/folder?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
        Vault.toast(`${result.staged_deletes} file(s) staged for deletion in CR #${result.cr_id}`, 'success');
    } catch (err) {
        Vault.toast(err.message, 'danger');
    }
}

// Sharing actions
async function togglePublic(path) {
    try {
        const data = await Vault.api(`/api/files/toggle-public?path=${encodeURIComponent(path)}`, { method: 'POST' });
        if (data.is_public) {
            Vault.toastPublic('Public link enabled');
        } else {
            Vault.toastPublic('Public link disabled');
        }
    } catch (err) {
        Vault.toast(err.message, 'danger');
    }
}

async function toggleArchive(path) {
    try {
        const data = await Vault.api(`/api/files/toggle-archive?path=${encodeURIComponent(path)}`, { method: 'POST' });
        if (data.is_archived) {
            Vault.toast('File archived', 'success');
        } else {
            Vault.toast('File unarchived', 'success');
        }
    } catch (err) {
        Vault.toast(err.message, 'danger');
    }
}

async function copyBucketUrl(path) {
    try {
        const data = await Vault.api(`/api/files/bucket-url?path=${encodeURIComponent(path)}`);
        await navigator.clipboard.writeText(data.url);
        Vault.toastPublic('Bucket URL copied to clipboard');
    } catch (err) {
        Vault.toast(err.message, 'danger');
    }
}

// File history
async function initHistory(filePath) {
    const container = document.getElementById('history-list');
    if (!container || !filePath) return;

    container.innerHTML = '<div class="loading-spinner"><div class="spinner-border spinner-border-sm me-2"></div> Loading...</div>';

    try {
        const versions = await Vault.api(`/api/files/history?path=${encodeURIComponent(filePath)}`);
        let html = '<table class="table table-hover table-sm"><thead><tr><th>Version</th><th>Author</th><th>Message</th><th>Size</th><th>Date</th><th>Actions</th></tr></thead><tbody>';

        for (const v of versions) {
            const diffBtn = v.version > 1 ? `<a href="/diff?path=${encodeURIComponent(filePath)}&old=${v.version - 1}&new=${v.version}" class="btn btn-xs btn-outline-secondary">Diff</a>` : '';
            const restoreBtn = !v.is_delete ? `<button class="btn btn-xs btn-outline-secondary" onclick="restoreVersion('${filePath}', ${v.version})">Restore</button>` : '';
            html += `<tr>
                <td><span class="mono">v${v.version}</span>${v.is_delete ? ' <span class="badge bg-danger">deleted</span>' : ''}</td>
                <td>${v.author}</td>
                <td class="truncate" style="max-width:300px">${v.message}</td>
                <td>${Vault.formatSize(v.size)}</td>
                <td title="${v.created_at}">${Vault.formatDate(v.created_at)}</td>
                <td>${diffBtn} ${restoreBtn}</td>
            </tr>`;
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger">${err.message}</div>`;
    }
}

async function restoreVersion(path, version) {
    if (!await Vault.confirm('Restore Version', `Stage restore of "${path}" to version ${version}? This will be added to your draft CR.`)) return;
    try {
        const result = await Vault.api(`/api/files/restore?path=${encodeURIComponent(path)}&version=${version}`, { method: 'POST' });
        Vault.toast(`Restore staged in CR #${result.cr_id}`, 'success');
    } catch (err) {
        Vault.toast(err.message, 'danger');
    }
}

// Diff viewer
async function initDiff(filePath, oldVer, newVer) {
    const container = document.getElementById('diff-content');
    if (!container || !filePath) return;

    container.innerHTML = '<div class="loading-spinner"><div class="spinner-border spinner-border-sm me-2"></div> Loading diff...</div>';

    try {
        const data = await Vault.api(`/api/files/diff?path=${encodeURIComponent(filePath)}&old=${oldVer}&new=${newVer}`);
        container.innerHTML = `<div class="diff-container">${data.diff_html}</div>`;
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger">${err.message}</div>`;
    }
}

// Change requests list
async function initChangeRequests(status = '') {
    const container = document.getElementById('cr-list');
    if (!container) return;

    container.innerHTML = '<div class="loading-spinner"><div class="spinner-border spinner-border-sm me-2"></div> Loading...</div>';

    try {
        const url = `/api/cr/list${status ? '?status=' + status : ''}`;
        const data = await Vault.api(url);

        if (data.items.length === 0) {
            container.innerHTML = '<div class="empty-state"><p>No change requests found</p></div>';
            return;
        }

        let html = '<table class="table table-hover"><thead><tr><th>#</th><th>Title</th><th>Status</th><th>Author</th><th>Files</th><th>Updated</th></tr></thead><tbody>';
        for (const cr of data.items) {
            html += `<tr class="cursor-pointer" onclick="window.location='/change-requests/${cr.id}'">
                <td class="mono">${cr.id}</td>
                <td>${cr.title}</td>
                <td>${Vault.statusBadge(cr.status)}</td>
                <td>${cr.author}</td>
                <td>${cr.file_count}</td>
                <td title="${cr.updated_at}">${Vault.formatDate(cr.updated_at)}</td>
            </tr>`;
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger">${err.message}</div>`;
    }
}

// CR detail
async function initCRDetail(crId) {
    const container = document.getElementById('cr-detail');
    if (!container) return;

    container.innerHTML = '<div class="loading-spinner"><div class="spinner-border spinner-border-sm me-2"></div> Loading...</div>';

    try {
        const cr = await Vault.api(`/api/cr/${crId}`);
        window._crData = cr;

        let filesHtml = '';
        for (const f of cr.files) {
            filesHtml += `<tr>
                <td>${Vault.actionBadge(f.action)}</td>
                <td class="mono">${f.file_path}</td>
                <td>${f.base_version ? 'v' + f.base_version : '-'}</td>
                <td>
                    <button class="btn btn-xs btn-outline-secondary" onclick="viewCRDiff(${crId}, ${f.id})">View Diff</button>
                    ${cr.status === 'draft' || cr.status === 'rejected' ? `<button class="btn btn-xs btn-danger" onclick="removeCRFile(${crId}, ${f.id})">Remove</button>` : ''}
                </td>
            </tr>`;
        }

        let actionsHtml = '';
        const userId = document.body.dataset.userId;
        const userRole = document.body.dataset.userRole;
        const isAuthor = String(cr.author_id) === String(userId);
        const canReview = userRole === 'admin' || (userRole === 'approver' && !isAuthor);

        if (cr.status === 'draft' || cr.status === 'rejected') {
            actionsHtml += `<button class="btn btn-sm btn-primary" onclick="submitCR(${crId})">Submit for Review</button> `;
            actionsHtml += `<button class="btn btn-sm btn-outline-secondary" onclick="window.location='/change-requests/${crId}/add-file'">Add File</button> `;
        }
        if (cr.status === 'pending_review' && canReview) {
            actionsHtml += `<button class="btn btn-sm btn-success" onclick="reviewCR(${crId}, 'approve')">Approve</button> `;
            actionsHtml += `<button class="btn btn-sm btn-danger" onclick="reviewCR(${crId}, 'reject')">Reject</button> `;
        }
        if (cr.status === 'pending_review' && isAuthor) {
            actionsHtml += `<span class="text-muted me-2" style="font-size:0.85rem">Awaiting review from an approver</span>`;
        }
        if (cr.status === 'approved') {
            actionsHtml += `<button class="btn btn-sm btn-success" onclick="mergeCR(${crId})">Merge</button> `;
        }
        if (cr.status !== 'merged' && cr.status !== 'closed') {
            actionsHtml += `<button class="btn btn-sm btn-outline-secondary" onclick="closeCR(${crId})">Close</button>`;
        }

        container.innerHTML = `
            <div class="d-flex justify-content-between align-items-start mb-3">
                <div>
                    <h4 class="mb-1">${cr.title} <span class="mono text-muted">#${cr.id}</span></h4>
                    <p class="text-secondary mb-0">${cr.description || '<em>No description</em>'}</p>
                </div>
                <div>${Vault.statusBadge(cr.status)}</div>
            </div>
            <div class="row mb-3">
                <div class="col-auto"><small class="text-muted">Author:</small> ${cr.author}</div>
                <div class="col-auto"><small class="text-muted">Reviewer:</small> ${cr.reviewer || '-'}</div>
                <div class="col-auto"><small class="text-muted">Created:</small> ${Vault.formatDate(cr.created_at)}</div>
                ${cr.review_comment ? `<div class="col-12 mt-2"><div class="alert alert-${cr.status === 'approved' ? 'success' : 'warning'} py-2"><strong>Review:</strong> ${cr.review_comment}</div></div>` : ''}
            </div>
            <div class="mb-3">${actionsHtml}</div>
            <div class="card">
                <div class="card-header">Files (${cr.files.length})</div>
                <div class="card-body p-0">
                    ${cr.files.length > 0 ? `<table class="table table-sm mb-0"><thead><tr><th>Action</th><th>Path</th><th>Base</th><th>Actions</th></tr></thead><tbody>${filesHtml}</tbody></table>` : '<div class="empty-state py-3"><p>No files in this change request</p></div>'}
                </div>
            </div>
            <div id="cr-diff-viewer" class="mt-3"></div>
        `;
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger">${err.message}</div>`;
    }
}

async function viewCRDiff(crId, fileId) {
    const container = document.getElementById('cr-diff-viewer');
    if (!container) return;
    container.innerHTML = '<div class="loading-spinner"><div class="spinner-border spinner-border-sm me-2"></div> Loading diff...</div>';
    try {
        const data = await Vault.api(`/api/cr/${crId}/diff/${fileId}`);
        container.innerHTML = `<div class="card"><div class="card-header">${Vault.actionBadge(data.action)} ${data.file_path}</div><div class="card-body p-0"><div class="diff-container">${data.diff_html}</div></div></div>`;
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger">${err.message}</div>`;
    }
}

async function submitCR(crId) {
    try {
        await Vault.api(`/api/cr/${crId}/submit`, { method: 'POST' });
        Vault.toast('Submitted for review', 'success');
        location.reload();
    } catch (err) { Vault.toast(err.message, 'danger'); }
}

async function reviewCR(crId, action) {
    const comment = prompt(`${action === 'approve' ? 'Approval' : 'Rejection'} comment (optional):`);
    if (comment === null) return;
    try {
        await Vault.api(`/api/cr/${crId}/review`, { method: 'POST', body: { action, comment } });
        Vault.toast(`Change request ${action}d`, 'success');
        location.reload();
    } catch (err) { Vault.toast(err.message, 'danger'); }
}

async function mergeCR(crId) {
    if (!await Vault.confirm('Merge CR', 'Apply all changes from this CR?')) return;
    try {
        await Vault.api(`/api/cr/${crId}/merge`, { method: 'POST' });
        Vault.toast('Change request merged', 'success');
        location.reload();
    } catch (err) { Vault.toast(err.message, 'danger'); }
}

async function closeCR(crId) {
    if (!await Vault.confirm('Close CR', 'Close this change request without merging?')) return;
    try {
        await Vault.api(`/api/cr/${crId}/close`, { method: 'POST' });
        Vault.toast('Change request closed', 'success');
        location.reload();
    } catch (err) { Vault.toast(err.message, 'danger'); }
}

async function removeCRFile(crId, fileId) {
    try {
        await Vault.api(`/api/cr/${crId}/files/${fileId}`, { method: 'DELETE' });
        Vault.toast('File removed from CR', 'success');
        location.reload();
    } catch (err) { Vault.toast(err.message, 'danger'); }
}

// Admin users
async function initAdminUsers() {
    const container = document.getElementById('users-list');
    if (!container) return;

    container.innerHTML = '<div class="loading-spinner"><div class="spinner-border spinner-border-sm me-2"></div> Loading...</div>';

    try {
        const data = await Vault.api('/api/admin/users');
        let html = '<table class="table table-hover"><thead><tr><th>Username</th><th>Email</th><th>Name</th><th>Role</th><th>Status</th><th>Joined</th><th>Actions</th></tr></thead><tbody>';
        for (const u of data.items) {
            html += `<tr>
                <td class="mono">${u.username}</td>
                <td>${u.email}</td>
                <td>${u.full_name}</td>
                <td>${Vault.roleBadge(u.role)}</td>
                <td>${u.is_active ? '<span class="badge bg-success">Active</span>' : '<span class="badge bg-secondary">Inactive</span>'}</td>
                <td>${Vault.formatDate(u.created_at)}</td>
                <td>
                    <select class="form-select form-select-sm d-inline-block" style="width:auto" onchange="updateUserRole(${u.id}, this.value)">
                        <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>Admin</option>
                        <option value="approver" ${u.role === 'approver' ? 'selected' : ''}>Approver</option>
                        <option value="editor" ${u.role === 'editor' ? 'selected' : ''}>Editor</option>
                        <option value="viewer" ${u.role === 'viewer' ? 'selected' : ''}>Viewer</option>
                    </select>
                    <button class="btn btn-xs btn-outline-secondary" onclick="toggleUserActive(${u.id}, ${!u.is_active})">${u.is_active ? 'Deactivate' : 'Activate'}</button>
                    <button class="btn btn-xs btn-outline-secondary" onclick="resetPassword(${u.id})">Reset PW</button>
                </td>
            </tr>`;
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger">${err.message}</div>`;
    }
}

async function updateUserRole(userId, role) {
    try {
        await Vault.api(`/api/admin/users/${userId}`, { method: 'PUT', body: { role } });
        Vault.toast('Role updated', 'success');
    } catch (err) { Vault.toast(err.message, 'danger'); location.reload(); }
}

async function toggleUserActive(userId, activate) {
    try {
        await Vault.api(`/api/admin/users/${userId}`, { method: 'PUT', body: { is_active: activate } });
        Vault.toast(activate ? 'User activated' : 'User deactivated', 'success');
        location.reload();
    } catch (err) { Vault.toast(err.message, 'danger'); }
}

async function resetPassword(userId) {
    const pw = prompt('Enter new password (min 8 characters):');
    if (!pw) return;
    try {
        await Vault.api(`/api/admin/users/${userId}/reset-password`, { method: 'POST', body: { new_password: pw } });
        Vault.toast('Password reset', 'success');
    } catch (err) { Vault.toast(err.message, 'danger'); }
}

// Audit log
async function initAuditLog() {
    const container = document.getElementById('audit-list');
    if (!container) return;

    container.innerHTML = '<div class="loading-spinner"><div class="spinner-border spinner-border-sm me-2"></div> Loading...</div>';

    try {
        const data = await Vault.api('/api/admin/audit-logs');
        let html = '<table class="table table-sm table-hover"><thead><tr><th>Time</th><th>User</th><th>Action</th><th>Resource</th><th>IP</th></tr></thead><tbody>';
        for (const l of data.items) {
            html += `<tr>
                <td title="${l.created_at}">${Vault.formatDate(l.created_at)}</td>
                <td>${l.user || '-'}</td>
                <td><span class="action-type">${l.action}</span></td>
                <td class="mono">${l.resource_type}/${l.resource_id}</td>
                <td class="text-muted">${l.ip_address}</td>
            </tr>`;
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger">${err.message}</div>`;
    }
}

// Search
async function initSearch() {
    const input = document.getElementById('search-input');
    const results = document.getElementById('search-results');
    if (!input || !results) return;

    let timeout;
    input.addEventListener('input', () => {
        clearTimeout(timeout);
        const q = input.value.trim();
        if (q.length < 2) { results.innerHTML = ''; results.style.display = 'none'; return; }
        timeout = setTimeout(async () => {
            try {
                const data = await Vault.api(`/api/files/search?q=${encodeURIComponent(q)}`);
                if (data.length === 0) {
                    results.innerHTML = '<div class="dropdown-item text-muted">No results</div>';
                } else {
                    results.innerHTML = data.map(f =>
                        `<a class="dropdown-item mono" href="/edit?path=${encodeURIComponent(f.path)}">${f.path} <small class="text-muted">v${f.version}</small></a>`
                    ).join('');
                }
                results.style.display = 'block';
            } catch (err) {
                results.innerHTML = '';
                results.style.display = 'none';
            }
        }, 300);
    });

    document.addEventListener('click', (e) => {
        if (!results.contains(e.target) && e.target !== input) {
            results.style.display = 'none';
        }
    });
}
