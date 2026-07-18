import { refreshAccessToken } from '@/api/client';
import { getAccessToken } from '@/lib/auth-store';

function attempt(
  workspaceId: string,
  file: File,
  onProgress: (fraction: number) => void,
): Promise<number> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append('file', file);
    xhr.open('POST', `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/documents`);
    xhr.withCredentials = true;
    const token = getAccessToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.status);
        return;
      }
      if (xhr.status === 401) {
        resolve(401);
        return;
      }
      let detail = `Upload failed (${xhr.status})`;
      try {
        const problem = JSON.parse(xhr.responseText) as { detail?: unknown };
        if (typeof problem.detail === 'string') detail = problem.detail;
      } catch {
        // Keep the status-based fallback when the upstream body is not JSON.
      }
      reject(new Error(detail));
    };
    xhr.onerror = () => reject(new Error('Network error during upload'));
    xhr.send(form);
  });
}

/** Upload each selected file using the backend's singular multipart `file` contract. */
export async function uploadDocuments(
  workspaceId: string,
  files: File[],
  onProgress: (percentage: number) => void,
): Promise<void> {
  if (!files.length) return;
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index]!;
    const report = (fraction: number) =>
      onProgress(Math.round(((index + fraction) / files.length) * 100));
    let status = await attempt(workspaceId, file, report);
    if (status === 401) {
      if (!(await refreshAccessToken())) throw new Error('Session expired');
      status = await attempt(workspaceId, file, report);
      if (status === 401) throw new Error('Session expired');
    }
    report(1);
  }
}
