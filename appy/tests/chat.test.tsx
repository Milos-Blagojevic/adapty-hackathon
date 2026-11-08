import { expect, it, jest } from '@jest/globals';
import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import ChatScreen from '../src/app/chat';
import { ApiError, MockAgentApi, type AgentApi } from '../src/lib/agentApi';
import { SupportProvider } from '../src/lib/support';

jest.useFakeTimers();

const METRICS = { frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } };

async function setup(api: AgentApi = new MockAgentApi({ replyMs: 100 })) {
  await render(
    <SafeAreaProvider initialMetrics={METRICS}>
      <SupportProvider api={api} pollMs={50} usersRetryMs={100}>
        <ChatScreen />
      </SupportProvider>
    </SafeAreaProvider>,
  );
  return api;
}

async function send(text: string) {
  await fireEvent.changeText(screen.getByLabelText('Message'), text);
  await fireEvent.press(screen.getByLabelText('Send'));
}

async function pickUser(id: string) {
  await fireEvent.press(screen.getByLabelText('Chat as'));
  await fireEvent.press(screen.getAllByText(id).at(-1)!);
}

it('answers known questions directly', async () => {
  await setup();
  expect(await screen.findByText('42')).toBeTruthy();
  await send('How do I reset my password?');
  expect(await screen.findByText(/Reset password/)).toBeTruthy();
});

it('hands off and shows the support reply from the session', async () => {
  await setup();
  await screen.findByText('42');
  await send('I was charged twice and want a refund');
  expect(await screen.findByText(/ticket T-1001/)).toBeTruthy();

  await act(async () => {
    jest.advanceTimersByTime(300);
  });
  expect(await screen.findByText(/Support teammate · T-1001/)).toBeTruthy();
  expect(screen.getByText(/ch_504/)).toBeTruthy();
});

it('keeps each user’s chat when switching users and back; New chat starts over', async () => {
  await setup();
  await screen.findByText('42');
  await send('How do I reset my password?');
  await screen.findByText(/Reset password/);

  await pickUser('user-7');
  expect(screen.queryByText(/Reset password/)).toBeNull();
  await pickUser('user-42');
  expect(screen.getByText(/Reset password/)).toBeTruthy();

  await fireEvent.press(screen.getByText('New chat'));
  expect(screen.queryByText(/Reset password/)).toBeNull();
});

it('a reply for another user’s chat does not land in the open chat', async () => {
  let release: (v: { reply: string; handed_off: boolean }) => void = () => {};
  const mock = new MockAgentApi();
  const api: AgentApi = {
    listUsers: () => mock.listUsers(),
    getSession: (id) => mock.getSession(id),
    sendMessage: () => new Promise((r) => (release = r)),
  };
  await setup(api);
  await screen.findByText('42');
  await send('slow question');
  expect(screen.getByLabelText('Assistant is typing')).toBeTruthy();
  await pickUser('user-7');
  await act(async () => release({ reply: 'late answer', handed_off: false }));
  expect(screen.queryByText('late answer')).toBeNull();
  await pickUser('user-42');
  expect(screen.getByText('late answer')).toBeTruthy();
});

it('a failed message can be retried; a reply that did arrive is not sent twice', async () => {
  const mock = new MockAgentApi();
  let fail = true;
  const sendSpy = jest.fn(async (u: string, s: string, m: string) => {
    if (fail) {
      fail = false;
      await mock.sendMessage(u, s, m); // the server got it, the answer was lost
      throw new ApiError('timeout', 'no answer in 60 s');
    }
    return mock.sendMessage(u, s, m);
  });
  const api: AgentApi = { listUsers: () => mock.listUsers(), getSession: (id) => mock.getSession(id), sendMessage: sendSpy };
  await setup(api);
  await screen.findByText('42');
  await send('How do I reset my password?');
  expect(await screen.findByText(/taking too long/)).toBeTruthy();
  await fireEvent.press(screen.getByLabelText('Try again'));
  expect(await screen.findByText(/Reset password/)).toBeTruthy();
  expect(screen.queryByText(/taking too long/)).toBeNull();
  expect(sendSpy).toHaveBeenCalledTimes(1);
});

it('retries loading users until the server is up', async () => {
  const mock = new MockAgentApi();
  let up = false;
  const api: AgentApi = {
    listUsers: async () => {
      if (!up) throw new ApiError('network', 'Failed to fetch');
      return mock.listUsers();
    },
    getSession: (id) => mock.getSession(id),
    sendMessage: (...a) => mock.sendMessage(...a),
  };
  await setup(api);
  expect(await screen.findByText(/Can't reach the support server/)).toBeTruthy();
  up = true;
  await act(async () => {
    jest.advanceTimersByTime(150);
  });
  expect(await screen.findByText('42')).toBeTruthy();
  expect(screen.queryByText(/Can't reach the support server/)).toBeNull();
});
