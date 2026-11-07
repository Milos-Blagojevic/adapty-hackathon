import { expect, it, jest } from '@jest/globals';
import { act, fireEvent, render, screen } from '@testing-library/react-native';

import ChatScreen from '../src/app/chat';
import { MockAgentApi } from '../src/lib/agentApi';
import { SupportProvider } from '../src/lib/support';

jest.useFakeTimers();

async function setup() {
  const api = new MockAgentApi({ replyMs: 100 });
  await render(
    <SupportProvider api={api} pollMs={50}>
      <ChatScreen />
    </SupportProvider>,
  );
  expect(await screen.findByText(/user-42 · active/)).toBeTruthy();
}

async function send(text: string) {
  await fireEvent.changeText(screen.getByLabelText('Message'), text);
  await fireEvent.press(screen.getByLabelText('Send'));
}

it('answers known questions directly', async () => {
  await setup();
  await send('How do I reset my password?');
  expect(await screen.findByText(/Reset password/)).toBeTruthy();
});

it('hands off and shows the support reply from the session', async () => {
  await setup();
  await send('I was charged twice and want a refund');
  expect(await screen.findByText(/ticket T-1001/)).toBeTruthy();

  await act(async () => {
    jest.advanceTimersByTime(300);
  });
  expect(await screen.findByText(/Support teammate · T-1001/)).toBeTruthy();
  expect(screen.getByText(/ch_504/)).toBeTruthy();
});

it('switching user starts a new conversation', async () => {
  await setup();
  await send('How do I reset my password?');
  await screen.findByText(/Reset password/);
  await fireEvent.press(screen.getByText(/user-7 · none/));
  expect(screen.queryByText(/Reset password/)).toBeNull();
});
