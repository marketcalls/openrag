import { setAccessToken } from '@/lib/auth-store';

import { uploadDocuments } from './upload';

class FakeXhr {
  static instances: FakeXhr[] = [];
  upload = {
    onprogress: null as
      | ((event: { lengthComputable: boolean; loaded: number; total: number }) => void)
      | null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  status = 201;
  responseText = '{}';
  headers: Record<string, string> = {};
  opened: [string, string] | null = null;
  body: FormData | null = null;

  open(method: string, url: string) {
    this.opened = [method, url];
  }

  setRequestHeader(key: string, value: string) {
    this.headers[key] = value;
  }

  send(body: FormData) {
    this.body = body;
    FakeXhr.instances.push(this);
  }
}

beforeEach(() => {
  FakeXhr.instances = [];
  vi.stubGlobal('XMLHttpRequest', FakeXhr as unknown as typeof XMLHttpRequest);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('sends the backend multipart file field with bearer auth and progress', async () => {
  setAccessToken('token');
  const onProgress = vi.fn();
  const promise = uploadDocuments('w1', [new File(['x'], 'a.pdf')], onProgress);
  const xhr = FakeXhr.instances[0]!;
  expect(xhr.opened).toEqual(['POST', '/api/v1/workspaces/w1/documents']);
  expect(xhr.headers.Authorization).toBe('Bearer token');
  expect((xhr.body?.get('file') as File).name).toBe('a.pdf');
  xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
  xhr.onload?.();
  await promise;
  expect(onProgress).toHaveBeenCalledWith(50);
  expect(onProgress).toHaveBeenLastCalledWith(100);
});

test('uploads multiple selected files sequentially with aggregate progress', async () => {
  const onProgress = vi.fn();
  const promise = uploadDocuments(
    'w1',
    [new File(['a'], 'a.pdf'), new File(['b'], 'b.pdf')],
    onProgress,
  );
  const first = FakeXhr.instances[0]!;
  first.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
  expect(onProgress).toHaveBeenLastCalledWith(25);
  first.onload?.();
  await vi.waitFor(() => expect(FakeXhr.instances).toHaveLength(2));
  const second = FakeXhr.instances[1]!;
  expect((second.body?.get('file') as File).name).toBe('b.pdf');
  second.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
  expect(onProgress).toHaveBeenLastCalledWith(75);
  second.onload?.();
  await promise;
  expect(onProgress).toHaveBeenLastCalledWith(100);
});

test('rejects with the server problem detail', async () => {
  const promise = uploadDocuments('w1', [new File(['x'], 'a.pdf')], vi.fn());
  const xhr = FakeXhr.instances[0]!;
  xhr.status = 415;
  xhr.responseText = JSON.stringify({ detail: 'unsupported file type' });
  xhr.onload?.();
  await expect(promise).rejects.toThrow('unsupported file type');
});

test('a 401 refreshes the access token and retries the file once', async () => {
  setAccessToken('stale');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ access_token: 'fresh' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  );
  const promise = uploadDocuments('w1', [new File(['x'], 'a.pdf')], vi.fn());
  const first = FakeXhr.instances[0]!;
  first.status = 401;
  first.onload?.();
  await vi.waitFor(() => expect(FakeXhr.instances).toHaveLength(2));
  const retry = FakeXhr.instances[1]!;
  expect(retry.headers.Authorization).toBe('Bearer fresh');
  retry.onload?.();
  await promise;
});
