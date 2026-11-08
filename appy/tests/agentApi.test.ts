import { describe, expect, it, jest } from '@jest/globals';

import { HttpAgentApi, MockAgentApi } from '../src/lib/agentApi';

describe('HttpAgentApi (OpenAPI contract)', () => {
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

  function setup(response: Response) {
    const fetchMock = jest.fn(async (_req: Request) => response);
    return { api: new HttpAgentApi('http://api.test/', fetchMock), fetchMock };
  }

  it('POST /chat with customer_user_id, session_id and message', async () => {
    const { api, fetchMock } = setup(json({ reply: 'Hi', handed_off: true, ticket_id: 'chat-1', actions: [] }));
    const result = await api.sendMessage('cus_1', 's-1', 'refund please');
    const req = fetchMock.mock.calls[0][0];
    expect(req.method).toBe('POST');
    expect(req.url).toBe('http://api.test/chat');
    expect(await req.json()).toEqual({ customer_user_id: 'cus_1', session_id: 's-1', message: 'refund please' });
    expect(result).toMatchObject({ reply: 'Hi', handed_off: true, ticket_id: 'chat-1' });
  });

  it('GET /api/chat/{session_id} encodes the session id', async () => {
    const session = { session_id: 'a b', customer_user_id: 'c', outcome: 'handed_off', messages: [], events: [] };
    const { api, fetchMock } = setup(json(session));
    expect(await api.getSession('a b')).toEqual(session);
    expect(fetchMock.mock.calls[0][0].url).toBe('http://api.test/api/chat/a%20b');
  });

  it('GET /api/users', async () => {
    const { api, fetchMock } = setup(json([{ customer_user_id: 'c1', badge: 'active', product: 'monthly' }]));
    expect(await api.listUsers()).toHaveLength(1);
    expect(fetchMock.mock.calls[0][0].url).toBe('http://api.test/api/users');
  });

  it('surfaces ErrorDetail and validation errors', async () => {
    await expect(setup(json({ detail: 'no session x' }, 404)).api.getSession('x')).rejects.toThrow('no session x');
    await expect(
      setup(json({ detail: [{ loc: ['body', 'message'], msg: 'Field required', type: 'missing' }] }, 422)).api.sendMessage('c', 's', ''),
    ).rejects.toThrow('Field required');
  });
});

describe('MockAgentApi', () => {
  it('answers FAQ questions without a handoff', async () => {
    const api = new MockAgentApi();
    const r = await api.sendMessage('user-42', 's1', 'How do I reset my password?');
    expect(r.handed_off).toBe(false);
    expect(r.reply).toMatch(/Reset password/);
  });

  it('hands off billing issues, then a support reply appears in the session', async () => {
    let now = 0;
    const api = new MockAgentApi({ replyMs: 10, now: () => now });
    const r = await api.sendMessage('user-42', 's1', 'I was charged twice this month');
    expect(r).toMatchObject({ handed_off: true, ticket_id: 'T-1001' });
    expect((await api.getSession('s1')).messages.some((m) => m.role === 'support')).toBe(false);

    now = 20;
    const s = await api.getSession('s1');
    expect(s.outcome).toBe('handed_off');
    const support = s.messages.filter((m) => m.role === 'support');
    expect(support).toHaveLength(1);
    expect(support[0].content).toContain('ch_504');
  });

  it('each instance has its own billing data', async () => {
    let now = 0;
    for (let i = 0; i < 2; i++) {
      const api = new MockAgentApi({ replyMs: 1, now: () => now });
      await api.sendMessage('user-42', 's', 'I was charged twice');
      now += 5;
      expect((await api.getSession('s')).messages.at(-1)?.content).toContain('ch_504');
    }
  });

  it('getSession on an unknown session fails like a 404', async () => {
    await expect(new MockAgentApi().getSession('nope')).rejects.toThrow('no session nope');
  });
});

describe('HttpAgentApi errors', () => {
  it('a network failure is an ApiError of kind network', async () => {
    const api = new HttpAgentApi('http://api.test', async () => {
      throw new TypeError('Network request failed');
    });
    await expect(api.listUsers()).rejects.toMatchObject({ kind: 'network' });
  });

  it('a request with no answer is aborted after the timeout', async () => {
    const hang = (req: Request) =>
      new Promise<Response>((_, reject) => req.signal.addEventListener('abort', () => reject(new Error('aborted'))));
    const api = new HttpAgentApi('http://api.test', hang, { chat: 20 });
    await expect(api.sendMessage('c', 's', 'hi')).rejects.toMatchObject({ kind: 'timeout' });
  });

  it('an HTTP error keeps its status', async () => {
    const api = new HttpAgentApi('http://api.test', async () => new Response('Internal Server Error', { status: 500 }));
    await expect(api.sendMessage('c', 's', 'hi')).rejects.toMatchObject({ kind: 'http', status: 500 });
  });
});
