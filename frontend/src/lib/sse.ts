export interface SseMessage {
  event: string;
  data: string;
}

/** Incremental parser for the subset of the WHATWG SSE grammar OpenRAG consumes. */
export function createSseParser(onMessage: (message: SseMessage) => void): {
  feed: (chunk: string) => void;
  flush: () => void;
} {
  let buffer = '';
  let event = 'message';
  let dataLines: string[] = [];

  const dispatch = () => {
    if (dataLines.length) onMessage({ event, data: dataLines.join('\n') });
    event = 'message';
    dataLines = [];
  };

  const processLine = (line: string) => {
    if (line === '') {
      dispatch();
      return;
    }
    if (line.startsWith(':')) return;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
  };

  return {
    feed(chunk: string) {
      buffer += chunk;
      for (;;) {
        const lineEnding = /\r\n|\n|\r/.exec(buffer);
        if (!lineEnding) break;
        if (lineEnding[0] === '\r' && lineEnding.index === buffer.length - 1) break;
        const line = buffer.slice(0, lineEnding.index);
        buffer = buffer.slice(lineEnding.index + lineEnding[0].length);
        processLine(line);
      }
    },
    flush() {
      if (buffer !== '') {
        processLine(buffer.replace(/\r$/, ''));
        buffer = '';
      }
      dispatch();
    },
  };
}
