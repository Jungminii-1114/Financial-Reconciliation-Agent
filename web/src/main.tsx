import React, { lazy, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import LiveApp from './LiveApp';
import './style.css';

const Prototype = lazy(() => import('./App'));
const showPrototype = new URLSearchParams(location.search).get('view') === 'prototype';
if (showPrototype) {
  document.documentElement.lang = 'en';
  document.title = 'Financial Reconciliation · UI Prototype';
}
createRoot(document.getElementById('root')!).render(
  showPrototype ? <Suspense fallback={null}><Prototype /></Suspense> : <LiveApp />,
);
