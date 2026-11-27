"""Tek kopya: uygulama zaten açıksa (ör. Windows ile başlamışsa) yeni kopya açılmaz, mevcut kopyaya haber verilir.

İki tür haber vardır:
  b"show"   : pencereyi öne getir (kullanıcı uygulamayı ikinci kez açtı)
  b"server" : yerel sunucuyu aç, pencereye dokunma (tarayıcı eklentisi ida:// ile çağırdı)
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    show_requested = Signal()
    server_requested = Signal()

    def __init__(self, key: str = "InternetDownloadAssistant.SingleInstance", parent=None):
        super().__init__(parent)
        self._key = key
        self._server = None

    def notify_running(self, message: bytes = b"show") -> bool:
        """Başka bir kopya çalışıyorsa ona haber verir ve True döner."""
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if socket.waitForConnected(300):
            socket.write(message)
            socket.flush()
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return True
        return False

    def listen(self) -> None:
        QLocalServer.removeServer(self._key)      # çökmeden kalan eski kaydı temizle
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        self._server.listen(self._key)

    def _on_connection(self) -> None:
        connection = self._server.nextPendingConnection()
        data = b""
        if connection is not None:
            if connection.bytesAvailable() or connection.waitForReadyRead(300):
                data = bytes(connection.readAll())
            connection.disconnectFromServer()
        if data.startswith(b"server"):
            self.server_requested.emit()
        else:
            self.show_requested.emit()
