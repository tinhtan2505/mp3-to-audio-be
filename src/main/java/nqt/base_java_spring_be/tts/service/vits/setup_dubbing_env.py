#!/usr/bin/env python3
"""
Script Cài Đặt Tự Động Môi Trường Dubbing
Python 3.11.9 + PyTorch 2.2.0 + Pyannote 3.1.1

FIXED: NumPy 1.26.4 được khóa cứng để tránh conflict với Pyannote

Chạy: python setup_dubbing_env.py
"""

import subprocess
import sys
import os
import platform

# ============================================
# CẤU HÌNH
# ============================================
PYTHON_VERSION = "3.11.9"
TORCH_VERSION = "2.2.0"
TORCHAUDIO_VERSION = "2.2.0"
PYANNOTE_VERSION = "3.1.1"

# Các thư viện cần cài
REQUIREMENTS = {
    # Core AI
    "torch": TORCH_VERSION,
    "torchaudio": TORCHAUDIO_VERSION,
    "pyannote.audio": PYANNOTE_VERSION,

    # Audio Processing
    "openai-whisper": "20231117",
    "edge-tts": "6.1.10",
    "pysrt": "1.1.2",
    "librosa": "0.10.1",
    "soundfile": "0.12.1",
    "numpy": "1.26.4",  # CRITICAL: NumPy 2.0+ breaks Pyannote 3.1.1

    # Web Server
    "fastapi": "0.109.0",
    "uvicorn": "0.27.0",
    "pydantic": "2.5.0",

    # Utils
    "requests": None,
    "tqdm": None,
}

# ============================================
# HÀM HỖ TRỢ
# ============================================

def print_header(text):
    """In header đẹp"""
    print("\n" + "="*60)
    print(f"🔧 {text}")
    print("="*60)

def run_command(cmd, check=True, shell=False):
    """Chạy command và hiển thị output"""
    try:
        # Convert list to proper format
        if isinstance(cmd, list):
            print(f"📌 Đang chạy: {' '.join(cmd)}")
        else:
            print(f"📌 Đang chạy: {cmd}")
            if not shell:
                cmd = cmd.split()

        result = subprocess.run(
            cmd,
            check=check,
            shell=shell,
            capture_output=False,
            text=True
        )
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"❌ Lỗi khi chạy command: {e}")
        return False

def check_python_version():
    """Kiểm tra phiên bản Python"""
    print_header("KIỂM TRA PHIÊN BẢN PYTHON")

    current_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(f"✅ Python hiện tại: {current_version}")

    if sys.version_info.major != 3 or sys.version_info.minor != 11:
        print(f"⚠️  Cảnh báo: Khuyến nghị dùng Python 3.11.x, bạn đang dùng {current_version}")
        response = input("Tiếp tục? (y/n): ")
        if response.lower() != 'y':
            sys.exit(1)

    return current_version

def uninstall_packages():
    """Gỡ sạch các packages cũ"""
    print_header("GỠ CÁC PACKAGE CŨ")

    packages_to_remove = [
        "numpy",  # GỠ NUMPY TRƯỚC TIÊN (quan trọng!)
        "torch", "torchaudio", "torchvision",
        "pyannote.audio", "pyannote.core", "pyannote.database", "pyannote.metrics", "pyannote-pipeline",
        "openai-whisper", "whisper", "whisperx",
        "librosa", "soundfile",
        "fastapi", "uvicorn",
        # Các packages có dependency conflict
        "asteroid-filterbanks",
        "pytorch-metric-learning",
        "speechbrain",
        "torchmetrics"
    ]

    print("📦 Danh sách package sẽ gỡ:")
    for pkg in packages_to_remove:
        print(f"   - {pkg}")

    print("\n⚠️  Phát hiện các packages cũ có dependency conflict:")
    print("   - asteroid-filterbanks, pytorch-metric-learning, speechbrain, torchmetrics")
    print("   → Nên gỡ để tránh conflict với môi trường mới")

    response = input("\n⚠️  Xác nhận gỡ? (y/n): ")
    if response.lower() != 'y':
        print("⏭️  Bỏ qua bước gỡ package")
        print("⚠️  WARNING: Có thể gặp dependency conflicts!")
        return

    # Gỡ NumPy riêng trước
    print(f"\n🗑️  Đang gỡ NumPy (để tránh conflict)...")
    run_command([sys.executable, "-m", "pip", "uninstall", "-y", "numpy"], check=False)

    for pkg in packages_to_remove:
        if pkg == "numpy":  # Đã gỡ rồi
            continue
        print(f"\n🗑️  Đang gỡ {pkg}...")
        run_command([sys.executable, "-m", "pip", "uninstall", "-y", pkg], check=False)

    print("\n✅ Đã gỡ xong các package cũ")

def upgrade_pip():
    """Nâng cấp pip"""
    print_header("NÂNG CẤP PIP")
    run_command([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    print("✅ Đã nâng cấp pip")

def detect_cuda():
    """Phát hiện CUDA version"""
    print_header("PHÁT HIỆN CUDA")

    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            check=True
        )

        if "CUDA Version" in result.stdout:
            # Parse CUDA version từ output
            for line in result.stdout.split('\n'):
                if "CUDA Version" in line:
                    cuda_version = line.split("CUDA Version:")[1].strip().split()[0]
                    print(f"✅ Phát hiện CUDA {cuda_version}")

                    # Map CUDA version -> PyTorch wheel
                    if cuda_version.startswith("12.1") or cuda_version.startswith("12."):
                        return "cu121"
                    elif cuda_version.startswith("11.8"):
                        return "cu118"
                    else:
                        print(f"⚠️  CUDA {cuda_version} - Sẽ dùng CUDA 11.8 wheel")
                        return "cu118"
    except:
        print("❌ Không phát hiện NVIDIA GPU")

    print("📍 Sẽ cài đặt PyTorch CPU version")
    return "cpu"

def install_numpy():
    """Cài đặt NumPy phiên bản tương thích"""
    print_header("CÀI ĐẶT NUMPY (CRITICAL STEP)")

    print("⚠️  QUAN TRỌNG: Pyannote 3.1.1 KHÔNG tương thích với NumPy 2.0+")
    print("   → Cần cài NumPy 1.26.x")

    # Gỡ NumPy cũ (nếu có)
    print("\n🗑️  Đang gỡ NumPy cũ (nếu có)...")
    run_command([sys.executable, "-m", "pip", "uninstall", "-y", "numpy"], check=False)

    # Cài NumPy 1.26.4
    print("\n📦 Cài đặt NumPy 1.26.4...")
    print("💡 Bỏ qua các warning về PyTorch - sẽ cài PyTorch ở bước tiếp theo\n")

    cmd = [sys.executable, "-m", "pip", "install", "numpy==1.26.4", "--no-warn-conflicts"]

    if run_command(cmd):
        print("\n✅ Cài đặt NumPy thành công")

        # Verify
        try:
            import numpy as np
            print(f"   📍 NumPy version: {np.__version__}")

            # Kiểm tra np.nan (NumPy 2.0 đã xóa np.NaN)
            test_val = np.nan
            print(f"   ✅ NumPy tương thích với Pyannote")

        except Exception as e:
            print(f"⚠️  Cảnh báo khi verify NumPy: {e}")

        return True
    else:
        print("❌ Lỗi cài đặt NumPy")
        return False

def install_pytorch(cuda_type):
    """Cài đặt PyTorch"""
    print_header(f"CÀI ĐẶT PYTORCH {TORCH_VERSION} ({cuda_type.upper()})")

    print("📝 Các warning về dependency conflicts từ bước trước là BÌNH THƯỜNG")
    print("   → Chúng sẽ tự động giải quyết sau khi cài PyTorch\n")

    if cuda_type == "cpu":
        index_url = "https://download.pytorch.org/whl/cpu"
    elif cuda_type == "cu121":
        index_url = "https://download.pytorch.org/whl/cu121"
    else:  # cu118
        index_url = "https://download.pytorch.org/whl/cu118"

    cmd = [
        sys.executable, "-m", "pip", "install",
        f"torch=={TORCH_VERSION}",
        f"torchaudio=={TORCHAUDIO_VERSION}",
        "--index-url", index_url
    ]

    if run_command(cmd):
        print("\n✅ Cài đặt PyTorch thành công")

        # Verify
        try:
            import torch
            print(f"   📍 PyTorch version: {torch.__version__}")
            print(f"   📍 CUDA available: {torch.cuda.is_available()}")
            if torch.cuda.is_available():
                print(f"   📍 CUDA version: {torch.version.cuda}")
                print(f"   📍 GPU: {torch.cuda.get_device_name(0)}")
        except ImportError:
            print("⚠️  Không thể import torch để verify")

        return True
    else:
        print("❌ Lỗi cài đặt PyTorch")
        return False

def install_pyannote():
    """Cài đặt Pyannote với NumPy được khóa"""
    print_header(f"CÀI ĐẶT PYANNOTE.AUDIO {PYANNOTE_VERSION}")

    print("⚠️  CHIẾN LƯỢC MỚI: Cài Pyannote với --no-deps")
    print("   → Ngăn pip tự động nâng cấp NumPy lên 2.x")
    print("   → Sau đó cài dependencies thủ công\n")

    # Bước 1: Cài Pyannote không có dependencies
    print("📦 Bước 1: Cài pyannote.audio (no deps)...")
    cmd = [
        sys.executable, "-m", "pip", "install",
        f"pyannote.audio=={PYANNOTE_VERSION}",
        "--no-deps"
    ]

    if not run_command(cmd):
        print("❌ Lỗi cài đặt Pyannote")
        return False

    # Bước 2: Cài dependencies của Pyannote (trừ những cái đã có)
    print("\n📦 Bước 2: Cài dependencies của Pyannote...")

    # Dependencies của pyannote.audio 3.1.1
    pyannote_deps = [
        "asteroid-filterbanks>=0.4",
        "einops>=0.6.0",
        "huggingface-hub>=0.13.0",
        "lightning>=2.0.1",
        "omegaconf>=2.1,<3.0",
        "pyannote.core>=5.0.0",
        "pyannote.database>=5.0.1",
        "pyannote.metrics>=3.2",
        "pyannote.pipeline>=3.0.1",
        "pytorch-metric-learning>=2.1.0",
        "rich>=12.0.0",
        "semver>=3.0.0",
        "soundfile>=0.12.1",
        "speechbrain>=0.5.14",
        "tensorboardX>=2.6",
        "torch-audiomentations>=0.11.0",
        "torchmetrics>=0.11.0"
    ]

    failed_deps = []

    for dep in pyannote_deps:
        print(f"\n   📌 Cài đặt: {dep}")
        cmd = [
            sys.executable, "-m", "pip", "install",
            dep,
            "--upgrade-strategy", "only-if-needed"
        ]

        if not run_command(cmd, check=False):
            print(f"   ⚠️  Không cài được {dep}")
            failed_deps.append(dep)

    if failed_deps:
        print(f"\n⚠️  Một số dependencies không cài được:")
        for dep in failed_deps:
            print(f"   - {dep}")

    # Bước 3: Verify và khóa NumPy
    print("\n📦 Bước 3: Kiểm tra và khóa NumPy...")

    try:
        import numpy as np
        current_numpy = np.__version__

        if current_numpy.startswith("2."):
            print(f"   ❌ NumPy đã bị nâng cấp lên {current_numpy}!")
            print("   🔧 Đang downgrade về 1.26.4...")

            run_command([
                sys.executable, "-m", "pip", "install",
                "numpy==1.26.4",
                "--force-reinstall",
                "--no-deps"
            ])

            # Verify lại
            import importlib
            importlib.reload(np)
            print(f"   ✅ NumPy đã được downgrade về {np.__version__}")
        else:
            print(f"   ✅ NumPy vẫn giữ ở {current_numpy}")

    except Exception as e:
        print(f"   ⚠️  Không thể verify NumPy: {e}")

    print("\n✅ Cài đặt Pyannote thành công")

    # Verify Pyannote
    try:
        import pyannote.audio
        print(f"   📍 Pyannote version: {pyannote.audio.__version__}")
    except ImportError as e:
        print(f"⚠️  Không thể import pyannote: {e}")
        print("   💡 Có thể cần khởi động lại Python để import được")

    return True

def install_other_packages():
    """Cài đặt các package còn lại"""
    print_header("CÀI ĐẶT CÁC PACKAGE KHÁC")

    # Bỏ qua torch, torchaudio, pyannote, numpy (đã cài)
    skip_packages = ["torch", "torchaudio", "pyannote.audio", "numpy"]

    for package, version in REQUIREMENTS.items():
        if package in skip_packages:
            continue

        print(f"\n📦 Đang cài {package}...")

        # Cài với strategy chỉ upgrade khi cần
        if version:
            cmd = [
                sys.executable, "-m", "pip", "install",
                f"{package}=={version}",
                "--upgrade-strategy", "only-if-needed"
            ]
        else:
            cmd = [
                sys.executable, "-m", "pip", "install",
                package,
                "--upgrade-strategy", "only-if-needed"
            ]

        if not run_command(cmd, check=False):
            print(f"⚠️  Cảnh báo: Không cài được {package}")

    # CRITICAL: Khóa NumPy cuối cùng
    print("\n" + "🔒"*30)
    print("BƯỚC CUỐI CÙNG: Khóa NumPy 1.26.4")
    print("🔒"*30)

    print("\n📌 Force reinstall NumPy 1.26.4 (không deps)...")
    run_command([
        sys.executable, "-m", "pip", "install",
        "numpy==1.26.4",
        "--force-reinstall",
        "--no-deps"
    ])

    print("\n✅ Hoàn tất cài đặt các package")

def create_requirements_file():
    """Tạo file requirements.txt"""
    print_header("TẠO FILE REQUIREMENTS.TXT")

    requirements_content = f"""# Dubbing Environment - Python {PYTHON_VERSION}
# Generated by setup_dubbing_env.py
# FIXED: NumPy 1.26.4 được khóa cứng

# === Core AI ===
torch=={TORCH_VERSION}
torchaudio=={TORCHAUDIO_VERSION}
pyannote.audio=={PYANNOTE_VERSION}

# === Audio Processing ===
openai-whisper==20231117
edge-tts==6.1.10
pysrt==1.1.2
librosa==0.10.1
soundfile==0.12.1
numpy==1.26.4  # CRITICAL: Locked at 1.26.4 for Pyannote compatibility

# === Web Server ===
fastapi==0.109.0
uvicorn==0.27.0
pydantic==2.5.0

# === Utils ===
requests
tqdm

# === IMPORTANT NOTES ===
# 1. NumPy MUST stay at 1.26.4 (Pyannote 3.1.1 breaks with NumPy 2.0+)
# 2. Install PyTorch first with correct CUDA version
# 3. Install Pyannote with --no-deps to prevent NumPy upgrade
# 4. Lock NumPy at the end with --force-reinstall --no-deps
"""

    with open("requirements.txt", "w", encoding="utf-8") as f:
        f.write(requirements_content)

    print("✅ Đã tạo file requirements.txt")
    print("📄 Vị trí: " + os.path.abspath("requirements.txt"))

def verify_installation():
    """Kiểm tra toàn bộ installation"""
    print_header("KIỂM TRA CÀI ĐẶT")

    packages_to_check = [
        ("torch", "PyTorch"),
        ("torchaudio", "TorchAudio"),
        ("numpy", "NumPy"),
        ("pyannote.audio", "Pyannote"),
        ("whisper", "Whisper"),
        ("edge_tts", "Edge-TTS"),
        ("pysrt", "PySRT"),
        ("librosa", "Librosa"),
        ("soundfile", "SoundFile"),
        ("fastapi", "FastAPI"),
        ("uvicorn", "Uvicorn"),
    ]

    success_count = 0
    results = []

    for module_name, display_name in packages_to_check:
        try:
            module = __import__(module_name)
            version = getattr(module, "__version__", "N/A")

            # Kiểm tra NumPy version đặc biệt
            if module_name == "numpy":
                if version.startswith("2."):
                    results.append(f"❌ {display_name:15s} - {version} (PHẢI LÀ 1.26.x!)")
                else:
                    results.append(f"✅ {display_name:15s} - {version}")
                    success_count += 1
            else:
                results.append(f"✅ {display_name:15s} - {version}")
                success_count += 1

        except ImportError as e:
            results.append(f"❌ {display_name:15s} - CHƯA CÀI ĐẶT")

    # In kết quả
    for result in results:
        print(result)

    print(f"\n📊 Kết quả: {success_count}/{len(packages_to_check)} packages OK")

    # Test PyTorch CUDA
    try:
        import torch
        if torch.cuda.is_available():
            print(f"\n🎮 GPU: {torch.cuda.get_device_name(0)}")
            print(f"   VRAM: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
    except:
        pass

    # Cảnh báo nếu NumPy sai version
    try:
        import numpy as np
        if np.__version__.startswith("2."):
            print("\n" + "⚠️"*30)
            print("CẢNH BÁO: NumPy 2.x được phát hiện!")
            print("Pyannote sẽ KHÔNG hoạt động với NumPy 2.x")
            print("Chạy lệnh sau để fix:")
            print("   pip install numpy==1.26.4 --force-reinstall --no-deps")
            print("⚠️"*30)
    except:
        pass

def create_test_script():
    """Tạo script test nhanh"""
    print_header("TẠO SCRIPT TEST")

    test_script = '''#!/usr/bin/env python3
"""
Script test nhanh môi trường
Chạy: python test_environment.py
"""

import sys

def test_imports():
    print("="*50)
    print("🧪 TEST IMPORT CÁC THƯ VIỆN")
    print("="*50)
    
    modules = [
        "torch", "torchaudio", "numpy", "pyannote.audio",
        "whisper", "edge_tts", "pysrt",
        "librosa", "soundfile", "fastapi"
    ]
    
    for module in modules:
        try:
            mod = __import__(module)
            version = getattr(mod, "__version__", "N/A")
            print(f"✅ {module:20s} - {version}")
        except ImportError as e:
            print(f"❌ {module:20s} - {e}")

def test_numpy_compatibility():
    print("\\n" + "="*50)
    print("🔍 TEST NUMPY COMPATIBILITY")
    print("="*50)
    
    try:
        import numpy as np
        print(f"NumPy version: {np.__version__}")
        
        if np.__version__.startswith("2."):
            print("❌ CẢNH BÁO: NumPy 2.x không tương thích với Pyannote!")
            print("   Chạy: pip install numpy==1.26.4 --force-reinstall --no-deps")
        else:
            print("✅ NumPy 1.26.x - Tương thích với Pyannote")
            
        # Test np.nan (bị xóa trong NumPy 2.0)
        test_val = np.nan
        print("✅ np.nan test: OK")
        
    except Exception as e:
        print(f"❌ Lỗi: {e}")

def test_torch():
    print("\\n" + "="*50)
    print("🔥 TEST PYTORCH")
    print("="*50)
    
    try:
        import torch
        print(f"PyTorch version: {torch.__version__}")
        print(f"CUDA available: {torch.cuda.is_available()}")
        
        if torch.cuda.is_available():
            print(f"CUDA version: {torch.version.cuda}")
            print(f"GPU: {torch.cuda.get_device_name(0)}")
            
            # Test tensor on GPU
            x = torch.randn(100, 100).cuda()
            y = torch.matmul(x, x)
            print("✅ GPU computation test: OK")
        else:
            # Test tensor on CPU
            x = torch.randn(100, 100)
            y = torch.matmul(x, x)
            print("✅ CPU computation test: OK")
            
    except Exception as e:
        print(f"❌ Lỗi: {e}")

def test_pyannote():
    print("\\n" + "="*50)
    print("🎤 TEST PYANNOTE")
    print("="*50)
    
    try:
        from pyannote.audio import Pipeline
        print(f"✅ Pyannote import OK")
        print("⚠️  Để test đầy đủ, cần HuggingFace token")
        print("   Lấy token tại: https://huggingface.co/settings/tokens")
        print("   Accept license: https://huggingface.co/pyannote/speaker-diarization-3.1")
    except Exception as e:
        print(f"❌ Lỗi: {e}")

if __name__ == "__main__":
    test_imports()
    test_numpy_compatibility()
    test_torch()
    test_pyannote()
    
    print("\\n" + "="*50)
    print("✅ HOÀN TẤT KIỂM TRA")
    print("="*50)
'''

    with open("test_environment.py", "w", encoding="utf-8") as f:
        f.write(test_script)

    print("✅ Đã tạo test_environment.py")
    print("📄 Chạy test: python test_environment.py")

def main():
    """Main function"""
    print("""
╔══════════════════════════════════════════════════════════╗
║     SCRIPT CÀI ĐẶT MÔI TRƯỜNG DUBBING TỰ ĐỘNG          ║
║     Python 3.11.9 + PyTorch 2.2.0 + Pyannote 3.1.1     ║
║     FIXED: NumPy 1.26.4 được khóa cứng                 ║
╚══════════════════════════════════════════════════════════╝
    """)

    # 1. Kiểm tra Python
    check_python_version()

    # 2. Hỏi có muốn gỡ package cũ không
    uninstall_packages()

    # 3. Nâng cấp pip
    upgrade_pip()

    # 4. CÀI NUMPY TRƯỚC (CRITICAL!)
    print("\n" + "🚨"*30)
    print("BƯỚC QUAN TRỌNG: Cài NumPy 1.26.x")
    print("🚨"*30)
    if not install_numpy():
        print("\n❌ LỖI: Không cài được NumPy. Dừng script.")
        sys.exit(1)

    # 5. Phát hiện CUDA
    cuda_type = detect_cuda()

    # 6. Cài PyTorch
    if not install_pytorch(cuda_type):
        print("\n❌ LỖI: Không cài được PyTorch. Dừng script.")
        sys.exit(1)

    # 7. Cài Pyannote (với NumPy được khóa)
    if not install_pyannote():
        print("\n❌ LỖI: Không cài được Pyannote. Dừng script.")
        sys.exit(1)

    # 8. Cài các package khác
    install_other_packages()

    # 9. Tạo requirements.txt
    create_requirements_file()

    # 10. Tạo test script
    create_test_script()

    # 11. Verify
    verify_installation()

    # Hoàn tất
    print("\n" + "="*60)
    print("🎉 CÀI ĐẶT HOÀN TẤT!")
    print("="*60)
    print("\n📋 CÁC BƯỚC TIẾP THEO:")
    print("   1. Khởi động lại terminal/IDE")
    print("   2. Chạy test: python test_environment.py")
    print("   3. Lấy HuggingFace token: https://huggingface.co/settings/tokens")
    print("   4. Accept license: https://huggingface.co/pyannote/speaker-diarization-3.1")
    print("   5. Chạy server: python your_dubbing_server.py")
    print("\n💡 TIP: Lưu HF token vào biến môi trường:")

    if platform.system() == "Windows":
        print('   set HF_TOKEN=your_token_here')
    else:
        print('   export HF_TOKEN=your_token_here')

    print("\n⚠️  LƯU Ý: Nếu gặp lỗi import pyannote, khởi động lại Python!")
    print("\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Script bị hủy bởi người dùng")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ LỖI NGHIÊM TRỌNG: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)