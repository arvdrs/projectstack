import { BrowserRouter, Routes, Route } from 'react-router-dom'
import LandingPage from './pages/LandingPage'
import AppPage from './pages/AppPage'
import SettingsPage from './pages/SettingsPage'
import ResultsPage from './pages/ResultsPage'
import ResultsRunPage from './pages/ResultsRunPage'
import ResultsLabelPage from './pages/ResultsLabelPage'
import ResultsComparePage from './pages/ResultsComparePage'
import Navigation from './components/Navigation'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/app" element={<><Navigation /><AppPage /></>} />
        <Route path="/analyze" element={<><Navigation /><ResultsPage /></>} />
        <Route path="/analyze/compare" element={<><Navigation /><ResultsComparePage /></>} />
        <Route path="/analyze/:runId" element={<><Navigation /><ResultsRunPage /></>} />
        <Route path="/analyze/:runId/label" element={<><Navigation /><ResultsLabelPage /></>} />
        <Route path="/settings" element={<><Navigation /><SettingsPage /></>} />
      </Routes>
    </BrowserRouter>
  )
}
