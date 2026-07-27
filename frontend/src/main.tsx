import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ReliabilityProvider } from './features/reliability'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8001'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ReliabilityProvider apiBaseUrl={API_BASE_URL}>
      <App />
    </ReliabilityProvider>
  </StrictMode>,
)
