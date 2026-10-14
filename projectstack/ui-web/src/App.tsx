import { BrowserRouter, Routes, Route } from 'react-router-dom'
import LandingPage from './pages/LandingPage'
import AppPage from './pages/AppPage'
import SettingsPage from './pages/SettingsPage'
import Navigation from './components/Navigation'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/app" element={<><Navigation /><AppPage /></>} />
        <Route path="/settings" element={<><Navigation /><SettingsPage /></>} />
      </Routes>
    </BrowserRouter>
  )
}
