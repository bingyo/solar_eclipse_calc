// すべての要求を 1 台のコンテナ（Dockerfile の run.py）に渡す Worker
import { Container, getContainer } from '@cloudflare/containers';

export class EclipseCalc extends Container {
  defaultPort = 8080;
  // 最後の要求からこの時間が過ぎると止まり、料金がかからなくなる（次の要求で起動する）
  sleepAfter = '15m';
}

function sameOrigin(request) {
  const origin = request.headers.get('Origin');
  if (!origin) return true;
  try {
    return new URL(origin).host === new URL(request.url).host;
  } catch {
    return false;  // "null" など
  }
}

export default {
  async fetch(request, env) {
    // 別のサイトからの POST を断る（server.py の _same_origin と同じ）。コンテナに届く Host は
    // 公開の名前と違うことがあるので、ここで確かめ、Origin を外して渡す
    if (request.method === 'POST' && !sameOrigin(request)) {
      return Response.json({ detail: 'Forbidden' }, { status: 403 });
    }
    const headers = new Headers(request.headers);
    headers.delete('Origin');
    return getContainer(env.ECLIPSE_CALC).fetch(new Request(request, { headers }));
  },
};
