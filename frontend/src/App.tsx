import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import WorldSelectPage from './pages/WorldSelectPage'
import SessionListPage from './pages/SessionListPage'
import CharacterCreatePage from './pages/CharacterCreatePage'
import GamePage from './pages/GamePage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<WorldSelectPage />} />
        <Route path="/:worldId/sessions" element={<SessionListPage />} />
        <Route path="/:worldId/sessions/:sid/create" element={<CharacterCreatePage />} />
        <Route path="/:worldId/sessions/:sid/play" element={<GamePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
