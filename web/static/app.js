const $ = (selector) => document.querySelector(selector);
let activeJob = null;

function value(id) { return $(id).value.trim(); }
function checked(id) { return $(id).checked; }

async function poll() {
  if (!activeJob) return;
  const response = await fetch(`/api/jobs/${activeJob}`);
  if (!response.ok) return;
  const job = await response.json();
  $('#status').textContent = job.status === 'running' ? '采集中' : job.status === 'completed' ? '已完成' : job.status === 'failed' ? '失败' : '排队中';
  $('#status').className = `badge ${job.status}`;
  $('#logs').textContent = job.logs.join('\n') || '正在启动……';
  $('#logs').scrollTop = $('#logs').scrollHeight;
  if (job.status === 'completed' || job.status === 'failed') {
    const text = job.status === 'completed' ? '任务已完成，可下载导出文件。' : '任务失败，请查看日志；断点数据仍保留在 data 文件夹。';
    $('#hint').textContent = text;
    $('#downloads').innerHTML = job.outputs.map((file) => `<a href="/download?job=${encodeURIComponent(job.id)}&file=${encodeURIComponent(file)}">下载 ${file}</a>`).join('');
    activeJob = null;
    $('#start').disabled = false;
    return;
  }
  window.setTimeout(poll, 1200);
}

$('#manual-login').addEventListener('change', () => {
  if (checked('#manual-login')) $('#headless').checked = false;
});
$('#headless').addEventListener('change', () => {
  if (checked('#headless')) $('#manual-login').checked = false;
});

$('#start').addEventListener('click', async () => {
  const formats = Array.from(document.querySelectorAll('.format:checked')).map((item) => item.value);
  const payload = {
    urls: value('#urls'), max_comments: value('#max-comments'), formats,
    include_replies: checked('#replies'), anonymize: checked('#anonymize'),
    anonymize_salt: value('#salt'), headless: checked('#headless'),
    manual_login: checked('#manual-login'), login_wait_seconds: value('#login-wait'),
  };
  $('#error').textContent = '';
  if (payload.manual_login && payload.headless) {
    $('#error').textContent = '手动登录不能与无界面运行同时使用。';
    return;
  }
  $('#start').disabled = true;
  try {
    const response = await fetch('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '无法创建任务');
    activeJob = result.id;
    $('#progress').classList.remove('hidden');
    $('#downloads').innerHTML = '';
    $('#hint').textContent = payload.manual_login ? '请在弹出的 Edge 中手动登录抖音；程序会在等待时间结束后继续。' : '浏览器采集任务已启动。';
    $('#logs').textContent = '';
    poll();
  } catch (error) {
    $('#error').textContent = error.message;
    $('#start').disabled = false;
  }
});
