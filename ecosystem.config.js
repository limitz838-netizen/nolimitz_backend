module.exports = {
  apps: [
    {
      name: "nolimitz-backend",
      script: "C:\\Users\\user\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 8000",
      cwd: "C:\\Users\\user\\Desktop\\nolimitz_backend",
      interpreter: "none",
      autorestart: true,
      watch: false
    },
    {
      name: "nolimitz-worker",
      script: "C:\\Users\\user\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
      args: "mt5_execution_worker.py",
      cwd: "C:\\Users\\user\\Desktop\\nolimitz_backend",
      interpreter: "none",
      autorestart: true,
      watch: false
    },
    {
      name: "nolimitz-verifier",
      script: "C:\\Users\\user\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
      args: "mt5_verify_service.py",
      cwd: "C:\\Users\\user\\Desktop\\nolimitz_backend",
      interpreter: "none",
      autorestart: true,
      watch: false
    },
    {
      name: "nolimitz-master-bridge",
      script: "C:\\Users\\user\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
      args: "master_robot_bridge.py",
      cwd: "C:\\Users\\user\\Desktop\\nolimitz_backend",
      interpreter: "none",
      autorestart: true,
      watch: false
    }
  ]
}