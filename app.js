const tabs = document.querySelectorAll('.tab');
const panels = document.querySelectorAll('.panel');
const jobsEl = document.querySelector('#jobs');
const statusEl = document.querySelector('#serverStatus');
const jobIds = new Set();

tabs.forEach(tab => {
  tab.addEventListener('click', () => {
    tabs.forEach(item => item.classList.remove('active'));
    panels.forEach(item => item.classList.remove('active'));
    tab.classList.add('active');
    document.querySelector('#' + tab.dataset.tab).classList.add('active');
  });
});

document.querySelector('#clearJobs').addEventListener('click', () => {
  jobIds.clear();
  renderJobs([]);
});

document.querySelector('#postForm').addEventListener('submit', event => {
  event.preventDefault();
  submitJob('/api/collect-posts', event.currentTarget);
});

document.querySelector('#topicForm').addEventListener('submit', event => {
  event.preventDefault();
  submitJob('/api/collect-topics', event.currentTarget);
});

function formPayload(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  for (const key of ['pages', 'post_pages', 'monitor_rounds']) {
    if (data[key] !== undefined) data[key] = Number(data[key] || 1);
  }
  for (const key of ['sleep', 'monitor_interval']) {
    if (data[key] !== undefined) data[key] = Number(data[key] || 0);
  }
  return data;
}

async function submitJob(url, form) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  statusEl.textContent = '启动中';
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(formPayload(form))
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '启动失败');
    jobIds.add(data.job_id);
    await pollJobs();
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    statusEl.textContent = '就绪';
  }
}

async function pollJobs() {
  if (jobIds.size === 0) {
    renderJobs([]);
    return;
  }
  const response = await fetch('/api/jobs?ids=' + encodeURIComponent([...jobIds].join(',')));
  const data = await response.json();
  renderJobs(data.jobs || []);
  const hasRunning = (data.jobs || []).some(job => job.status === 'running' || job.status === 'queued');
  statusEl.textContent = hasRunning ? '采集中' : '就绪';
  if (hasRunning) setTimeout(pollJobs, 1500);
}

function renderJobs(jobs) {
  if (!jobs.length) {
    jobsEl.innerHTML = '<div class="empty">还没有任务。填写上面的表单后，结果会出现在这里。</div>';
    return;
  }
  jobsEl.innerHTML = jobs.map(job => {
    const badgeClass = job.status === 'done' ? 'done' : job.status === 'error' ? 'error' : '';
    const statusText = { queued: '排队中', running: '运行中', done: '完成', error: '失败' }[job.status] || job.status;
    const downloads = (job.files || []).map(file =>
      `<a href="/download/${encodeURIComponent(file.name)}" download>${file.label}</a>`
    ).join('');
    const error = job.error ? `<pre class="error-text">${escapeHtml(job.error)}</pre>` : '';
    return `<article class="job">
      <div class="job-top">
        <div>
          <div class="job-title">${escapeHtml(job.title)}</div>
          <div class="job-meta">${escapeHtml(job.created_at)} · ${job.count || 0} 条结果</div>
        </div>
        <span class="badge ${badgeClass}">${statusText}</span>
      </div>
      ${downloads ? `<div class="downloads">${downloads}</div>` : ''}
      ${error}
    </article>`;
  }).join('');
}

function escapeHtml(value) {
  return String(value || '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

renderJobs([]);
