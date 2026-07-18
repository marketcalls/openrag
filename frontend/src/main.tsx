import React from 'react';
import ReactDOM from 'react-dom/client';

import { App } from './app';

const root = document.getElementById('root');

if (root === null) {
  throw new Error('OpenRAG root element is missing');
}

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
