import React from 'react';
import ReactDOM from 'react-dom/client';

import '@fontsource/manrope/400.css';
import '@fontsource/manrope/500.css';
import '@fontsource/manrope/600.css';
import '@fontsource/manrope/700.css';
import '@fontsource/manrope/800.css';

import { App } from './app';
import { applyTheme, resolveInitialTheme } from './lib/theme';
import './styles/globals.css';

applyTheme(resolveInitialTheme());

const root = document.getElementById('root');

if (root === null) {
  throw new Error('OpenRAG root element is missing');
}

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
