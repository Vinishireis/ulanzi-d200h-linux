import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import { isHosted, SiteGate } from './site';
import './styles.css';

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 2000, retry: 1, refetchInterval: 5000 } } });
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={queryClient}>{isHosted ? <SiteGate /> : <App />}</QueryClientProvider></React.StrictMode>);
