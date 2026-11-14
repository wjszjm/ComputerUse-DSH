#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dwmapi.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <fcntl.h>
#include <io.h>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <memory>
#include <vector>

using namespace winrt;
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;
using namespace winrt::Windows::Graphics::DirectX::Direct3D11;

namespace {
template<class T> struct CloseCapture {
    T const& object;
    ~CloseCapture() { try { object.Close(); } catch (...) {} }
};

struct Request {
    HWND hwnd;
    DWORD pid;
    RECT bounds;
    RECT crop;
};

bool eligible(Request const& request) {
    DWORD pid{}, affinity{}, cloaked{};
    RECT rect{};
    return IsWindow(request.hwnd) && IsWindowVisible(request.hwnd) && !IsIconic(request.hwnd) &&
        GetWindowThreadProcessId(request.hwnd, &pid) && pid == request.pid &&
        SUCCEEDED(DwmGetWindowAttribute(request.hwnd, DWMWA_CLOAKED, &cloaked, sizeof(cloaked))) &&
        !cloaked && GetWindowDisplayAffinity(request.hwnd, &affinity) && affinity == WDA_NONE &&
        GetWindowRect(request.hwnd, &rect) && EqualRect(&rect, &request.bounds);
}

struct CaptureDevice {
    com_ptr<ID3D11Device> native;
    com_ptr<ID3D11DeviceContext> context;
    IDirect3DDevice device{nullptr};

    CaptureDevice() {
        auto status = D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_HARDWARE,nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,nullptr,0,D3D11_SDK_VERSION,native.put(),nullptr,context.put());
        if (FAILED(status)) {
            native = nullptr;
            context = nullptr;
            check_hresult(D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_WARP,nullptr,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,nullptr,0,D3D11_SDK_VERSION,native.put(),nullptr,context.put()));
        }
        com_ptr<IInspectable> inspectable;
        check_hresult(CreateDirect3D11DeviceFromDXGIDevice(native.as<IDXGIDevice>().get(),inspectable.put()));
        device = inspectable.as<IDirect3DDevice>();
    }

    std::vector<char> frame(Request const& request) {
        if (!eligible(request) || !GraphicsCaptureSession::IsSupported()) return {};
        RECT visible{};
        if (FAILED(DwmGetWindowAttribute(request.hwnd,DWMWA_EXTENDED_FRAME_BOUNDS,&visible,sizeof(visible))) ||
            visible.left < request.bounds.left || visible.top < request.bounds.top ||
            visible.right > request.bounds.right || visible.bottom > request.bounds.bottom) return {};
        RECT overlap{};
        if (!IntersectRect(&overlap,&visible,&request.crop)) return {};
        const auto x = overlap.left-visible.left;
        const auto y = overlap.top-visible.top;
        const auto width = overlap.right-overlap.left;
        const auto height = overlap.bottom-overlap.top;
        const auto output_width = request.crop.right-request.crop.left;
        const auto output_height = request.crop.bottom-request.crop.top;
        if (static_cast<int64_t>(output_width)*output_height > 16000000) return {};

        auto interop = get_activation_factory<GraphicsCaptureItem,IGraphicsCaptureItemInterop>();
        GraphicsCaptureItem item{nullptr};
        check_hresult(interop->CreateForWindow(request.hwnd,
            guid_of<IGraphicsCaptureItem>(),put_abi(item)));
        const auto size = item.Size();
        if (size.Width != visible.right-visible.left || size.Height != visible.bottom-visible.top ||
            size.Width <= 0 || size.Height <= 0 || static_cast<int64_t>(size.Width)*size.Height > 16000000) return {};

        auto pool = Direct3D11CaptureFramePool::CreateFreeThreaded(device,
            DirectXPixelFormat::B8G8R8A8UIntNormalized,2,size);
        CloseCapture close_pool{pool};
        auto session = pool.CreateCaptureSession(item);
        CloseCapture close_session{session};
        try { session.IsCursorCaptureEnabled(false); } catch (hresult_error const&) {}
        // Preserve the Windows capture indicator and do not request borderless capture.
        auto arrived = std::make_shared<std::atomic_bool>(false);
        auto on_frame = pool.FrameArrived(auto_revoke,[arrived](auto&&,auto&&) { arrived->store(true); });
        session.StartCapture();
        const auto until = std::chrono::steady_clock::now()+std::chrono::milliseconds(900);
        while (!arrived->load() && std::chrono::steady_clock::now() < until) Sleep(8);
        auto frame = pool.TryGetNextFrame();
        if (!frame) return {};
        CloseCapture close_frame{frame};
        if (frame.ContentSize().Width != size.Width || frame.ContentSize().Height != size.Height) return {};
        auto access = frame.Surface().as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
        com_ptr<ID3D11Texture2D> texture;
        check_hresult(access->GetInterface(__uuidof(ID3D11Texture2D),texture.put_void()));
        D3D11_TEXTURE2D_DESC desc{};
        texture->GetDesc(&desc);
        if (desc.Width < static_cast<UINT>(x+width) || desc.Height < static_cast<UINT>(y+height) ||
            desc.Format != DXGI_FORMAT_B8G8R8A8_UNORM) return {};
        desc.Width = width;
        desc.Height = height;
        desc.Usage = D3D11_USAGE_STAGING;
        desc.BindFlags = 0;
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        desc.MiscFlags = 0;
        com_ptr<ID3D11Texture2D> pixels;
        check_hresult(native->CreateTexture2D(&desc,nullptr,pixels.put()));
        const D3D11_BOX box{static_cast<UINT>(x),static_cast<UINT>(y),0,
            static_cast<UINT>(x+width),static_cast<UINT>(y+height),1};
        context->CopySubresourceRegion(pixels.get(),0,0,0,0,texture.get(),0,&box);
        const uint32_t dimensions[] = {static_cast<uint32_t>(output_width),static_cast<uint32_t>(output_height)};
        const auto row_bytes = static_cast<size_t>(width)*4;
        const auto stride = static_cast<size_t>(output_width)*4;
        const auto offset = sizeof(dimensions)+stride*(overlap.top-request.crop.top)+4*(overlap.left-request.crop.left);
        std::vector<char> result(sizeof(dimensions)+stride*output_height);
        std::memcpy(result.data(),dimensions,sizeof(dimensions));
        D3D11_MAPPED_SUBRESOURCE mapped{};
        check_hresult(context->Map(pixels.get(),0,D3D11_MAP_READ,0,&mapped));
        for (int row=0; row<height; ++row) {
            std::memcpy(result.data()+offset+stride*row,
                static_cast<char const*>(mapped.pData)+static_cast<size_t>(row)*mapped.RowPitch,row_bytes);
        }
        context->Unmap(pixels.get(),0);
        RECT after{};
        if (!eligible(request) || FAILED(DwmGetWindowAttribute(request.hwnd,DWMWA_EXTENDED_FRAME_BOUNDS,
                &after,sizeof(after))) || !EqualRect(&visible,&after)) return {};
        return result;
    }
};
}

int main(int argc,char** argv) {
    if (argc != 2) return 2;
    const auto pid = strtoul(argv[1],nullptr,10);
    const auto owner = OpenProcess(SYNCHRONIZE,FALSE,pid);
    if (!owner) return 2;
    const auto owner_watch = CreateThread(nullptr,0,[](void* handle) -> DWORD {
        WaitForSingleObject(handle,INFINITE);
        TerminateProcess(GetCurrentProcess(),0);
        return 0;
    },owner,0,nullptr);
    if (!owner_watch) return 2;
    CloseHandle(owner_watch);
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    init_apartment(apartment_type::multi_threaded);
    _setmode(_fileno(stdin),_O_BINARY);
    _setmode(_fileno(stdout),_O_BINARY);
    // The protocol is private fixed-width decimal values from the Python capture worker.
    std::unique_ptr<CaptureDevice> device;
    uint32_t length{};
    while (std::cin.read(reinterpret_cast<char*>(&length),sizeof(length)) && length <= 256) {
        std::string input(length,'\0');
        if (!std::cin.read(input.data(),input.size())) return 1;
        unsigned long long hwnd{};
        Request request{};
        std::vector<char> result;
        if (sscanf_s(input.c_str(),"\"%llu %lu %ld %ld %ld %ld %ld %ld %ld %ld\"", &hwnd, &request.pid,
                &request.bounds.left,&request.bounds.top,&request.bounds.right,&request.bounds.bottom,
                &request.crop.left,&request.crop.top,&request.crop.right,&request.crop.bottom) == 10) {
            request.hwnd = reinterpret_cast<HWND>(static_cast<uintptr_t>(hwnd));
            try {
                if (!device) device = std::make_unique<CaptureDevice>();
                result = device->frame(request);
            } catch (...) { device.reset(); }
        }
        length = static_cast<uint32_t>(result.size());
        std::cout.write(reinterpret_cast<char const*>(&length),sizeof(length));
        if (length) std::cout.write(result.data(),length);
        std::cout.flush();
    }
}
