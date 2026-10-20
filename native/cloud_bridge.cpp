// Remote Believe: small ABI bridge to the Windows Cloud Files API.
// Network/auth/upload policy stays in Python. No file data is staged twice.
#include <windows.h>
#include <winternl.h>
#include <cfapi.h>
#include <roapi.h>
#include <shobjidl.h>
#include <shlobj.h>
#include <atomic>
#include <algorithm>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Storage.h>
#include <winrt/Windows.Storage.Provider.h>

#define RB_API extern "C" __declspec(dllexport)
constexpr NTSTATUS kUnavailable = static_cast<NTSTATUS>(0xC000CF11L);
constexpr NTSTATUS kDenied = static_cast<NTSTATUS>(0xC000CF18L);
using Event = void (CALLBACK *)(int, unsigned long long, const void*, DWORD,
                               long long, long long, long long, const wchar_t*);
struct Provider { CF_CONNECTION_KEY key{}; Event callback{}; };
struct Request {
    CF_OPERATION_INFO operation{};
    int kind{};
    long long offset{}, length{};
    Provider* owner{};
};
std::mutex requestsMutex;
std::unordered_map<unsigned long long, std::shared_ptr<Request>> requests;
std::atomic<unsigned long long> nextRequest{1};

static std::shared_ptr<Request> Find(unsigned long long id) {
    std::lock_guard<std::mutex> lock(requestsMutex);
    auto it = requests.find(id);
    return it == requests.end() ? nullptr : it->second;
}
static void Forget(unsigned long long id) {
    std::lock_guard<std::mutex> lock(requestsMutex);
    requests.erase(id);
}
static CF_OPERATION_INFO Operation(const CF_CALLBACK_INFO* info, CF_OPERATION_TYPE type) {
    CF_OPERATION_INFO op{};
    op.StructSize = sizeof(op); op.Type = type;
    op.ConnectionKey = info->ConnectionKey; op.TransferKey = info->TransferKey;
    op.RequestKey = info->RequestKey;
    return op;
}
static void CALLBACK Fetch(const CF_CALLBACK_INFO* info, const CF_CALLBACK_PARAMETERS* params, int kind) {
    auto provider = static_cast<Provider*>(info->CallbackContext);
    auto req = std::make_shared<Request>();
    req->kind = kind; req->owner = provider;
    req->operation = Operation(info, kind == 0 ? CF_OPERATION_TYPE_TRANSFER_DATA : CF_OPERATION_TYPE_TRANSFER_PLACEHOLDERS);
    if (kind == 0) {
        req->offset = params->FetchData.RequiredFileOffset.QuadPart;
        req->length = params->FetchData.RequiredLength.QuadPart;
    }
    auto id = nextRequest.fetch_add(1);
    { std::lock_guard<std::mutex> lock(requestsMutex); requests.emplace(id, req); }
    auto process = info->ProcessInfo ? info->ProcessInfo->ImagePath : L"";
    provider->callback(kind, id, info->FileIdentity, info->FileIdentityLength,
                       info->FileSize.QuadPart, req->offset, req->length, process);
}
static void CALLBACK FetchData(const CF_CALLBACK_INFO* info, const CF_CALLBACK_PARAMETERS* params) { Fetch(info, params, 0); }
static void CALLBACK FetchFolders(const CF_CALLBACK_INFO* info, const CF_CALLBACK_PARAMETERS* params) { Fetch(info, params, 1); }
static void CALLBACK Cancel(const CF_CALLBACK_INFO* info, const CF_CALLBACK_PARAMETERS*) {
    std::lock_guard<std::mutex> lock(requestsMutex);
    for (auto it = requests.begin(); it != requests.end();) {
        const auto& op = it->second->operation;
        if (op.ConnectionKey.Internal == info->ConnectionKey.Internal &&
            op.TransferKey.QuadPart == info->TransferKey.QuadPart && op.RequestKey.QuadPart == info->RequestKey.QuadPart)
            it = requests.erase(it);
        else ++it;
    }
}
static bool IsMaya(const CF_CALLBACK_INFO* info) {
    if (!info->ProcessInfo || !info->ProcessInfo->ImagePath) return false;
    const wchar_t* name = wcsrchr(info->ProcessInfo->ImagePath, L'\\');
    name = name ? name + 1 : info->ProcessInfo->ImagePath;
    return _wcsicmp(name, L"maya.exe") == 0 || _wcsicmp(name, L"mayabatch.exe") == 0;
}
static void CALLBACK Delete(const CF_CALLBACK_INFO* info, const CF_CALLBACK_PARAMETERS*) {
    auto op = Operation(info, CF_OPERATION_TYPE_ACK_DELETE);
    CF_OPERATION_PARAMETERS data{};
    data.ParamSize = FIELD_OFFSET(CF_OPERATION_PARAMETERS, AckDelete) + sizeof(data.AckDelete);
    // Deletion affects the local copy only; the Office API has no delete operation.
    data.AckDelete.CompletionStatus = 0;
    CfExecute(&op, &data);
}
static void CALLBACK Removed(const CF_CALLBACK_INFO* info, const CF_CALLBACK_PARAMETERS*) {
    // A Maya save/replace is not a user instruction to forget the file.
    if (IsMaya(info) || (info->ProcessInfo && info->ProcessInfo->ProcessId == GetCurrentProcessId())) return;
    auto provider = static_cast<Provider*>(info->CallbackContext);
    provider->callback(2, 0, info->FileIdentity, info->FileIdentityLength, info->FileSize.QuadPart, 0, 0,
                       info->ProcessInfo ? info->ProcessInfo->ImagePath : L"");
}
static void CALLBACK Rename(const CF_CALLBACK_INFO* info, const CF_CALLBACK_PARAMETERS* params) {
    auto op = Operation(info, CF_OPERATION_TYPE_ACK_RENAME);
    CF_OPERATION_PARAMETERS data{};
    data.ParamSize = FIELD_OFFSET(CF_OPERATION_PARAMETERS, AckRename) + sizeof(data.AckRename);
    bool saving = IsMaya(info) && !(params->Rename.Flags & CF_CALLBACK_RENAME_FLAG_IS_DIRECTORY) &&
                  (params->Rename.Flags & CF_CALLBACK_RENAME_FLAG_TARGET_IN_SCOPE);
    bool provider = info->ProcessInfo && info->ProcessInfo->ProcessId == GetCurrentProcessId();
    // Moving to the Recycle Bin or outside the cache is also a local removal.
    bool leaving = (params->Rename.Flags & CF_CALLBACK_RENAME_FLAG_SOURCE_IN_SCOPE) &&
                   !(params->Rename.Flags & CF_CALLBACK_RENAME_FLAG_TARGET_IN_SCOPE);
    data.AckRename.CompletionStatus = (saving || provider || leaving) ? 0 : kDenied;
    CfExecute(&op, &data);
}

RB_API HRESULT RBConnect(const wchar_t* path, const void* identity, DWORD identityLength, Event callback, void** result) {
    if (!path || !callback || !result) return E_INVALIDARG;
    *result = nullptr;
    CF_SYNC_REGISTRATION registration{};
    registration.StructSize = sizeof(registration);
    registration.ProviderName = L"Remote Believe"; registration.ProviderVersion = L"3.1.0";
    registration.SyncRootIdentity = identity; registration.SyncRootIdentityLength = identityLength;
    registration.FileIdentity = identity; registration.FileIdentityLength = identityLength;
    CF_SYNC_POLICIES policies{};
    policies.StructSize = sizeof(policies);
    policies.Hydration.Primary = CF_HYDRATION_POLICY_FULL;
    policies.Population.Primary = CF_POPULATION_POLICY_FULL;
    policies.InSync = CF_INSYNC_POLICY_TRACK_FILE_LAST_WRITE_TIME;
    auto flags = CF_REGISTER_FLAG_UPDATE | CF_REGISTER_FLAG_DISABLE_ON_DEMAND_POPULATION_ON_ROOT | CF_REGISTER_FLAG_MARK_IN_SYNC_ON_ROOT;
    HRESULT hr = CfRegisterSyncRoot(path, &registration, &policies, flags);
    if (FAILED(hr)) return hr;
    auto provider = std::make_unique<Provider>(); provider->callback = callback;
    CF_CALLBACK_REGISTRATION callbacks[] = {
        {CF_CALLBACK_TYPE_FETCH_DATA, FetchData}, {CF_CALLBACK_TYPE_FETCH_PLACEHOLDERS, FetchFolders},
        {CF_CALLBACK_TYPE_CANCEL_FETCH_DATA, Cancel}, {CF_CALLBACK_TYPE_CANCEL_FETCH_PLACEHOLDERS, Cancel},
        {CF_CALLBACK_TYPE_NOTIFY_DELETE, Delete}, {CF_CALLBACK_TYPE_NOTIFY_DELETE_COMPLETION, Removed},
        {CF_CALLBACK_TYPE_NOTIFY_RENAME, Rename}, {CF_CALLBACK_TYPE_NOTIFY_RENAME_COMPLETION, Removed}, CF_CALLBACK_REGISTRATION_END
    };
    hr = CfConnectSyncRoot(path, callbacks, provider.get(), CF_CONNECT_FLAG_REQUIRE_PROCESS_INFO | CF_CONNECT_FLAG_REQUIRE_FULL_FILE_PATH, &provider->key);
    if (FAILED(hr)) return hr;
    CfUpdateSyncProviderStatus(provider->key, CF_PROVIDER_STATUS_IDLE);
    *result = provider.release();
    return S_OK;
}
RB_API HRESULT RBFinish(unsigned long long id, LONG status) {
    auto req = Find(id); if (!req) return HRESULT_FROM_WIN32(ERROR_OPERATION_ABORTED);
    CF_OPERATION_PARAMETERS data{};
    if (req->kind == 0) {
        data.ParamSize = FIELD_OFFSET(CF_OPERATION_PARAMETERS, TransferData) + sizeof(data.TransferData);
        data.TransferData.CompletionStatus = status;
        data.TransferData.Offset.QuadPart = req->offset; data.TransferData.Length.QuadPart = req->length;
    } else {
        data.ParamSize = FIELD_OFFSET(CF_OPERATION_PARAMETERS, TransferPlaceholders) + sizeof(data.TransferPlaceholders);
        data.TransferPlaceholders.CompletionStatus = status;
        data.TransferPlaceholders.Flags = CF_OPERATION_TRANSFER_PLACEHOLDERS_FLAG_DISABLE_ON_DEMAND_POPULATION;
    }
    HRESULT hr = CfExecute(&req->operation, &data);
    Forget(id); return hr;
}
RB_API void RBDisconnect(void* handle) {
    auto provider = static_cast<Provider*>(handle); if (!provider) return;
    std::vector<unsigned long long> pending;
    { std::lock_guard<std::mutex> lock(requestsMutex);
      for (const auto& pair : requests) if (pair.second->owner == provider) pending.push_back(pair.first); }
    for (auto id : pending) RBFinish(id, kUnavailable);
    CfDisconnectSyncRoot(provider->key); delete provider;
}
RB_API HRESULT RBUnregister(const wchar_t* path) { return CfUnregisterSyncRoot(path); }
RB_API BOOL RBPending(unsigned long long id) { return Find(id) != nullptr; }
RB_API void RBRelease(unsigned long long id) { Forget(id); }
RB_API HRESULT RBTransfer(unsigned long long id, const void* buffer, long long offset, long long length) {
    auto req = Find(id); if (!req) return HRESULT_FROM_WIN32(ERROR_OPERATION_ABORTED);
    CF_OPERATION_PARAMETERS data{};
    data.ParamSize = FIELD_OFFSET(CF_OPERATION_PARAMETERS, TransferData) + sizeof(data.TransferData);
    data.TransferData.Offset.QuadPart = offset; data.TransferData.Length.QuadPart = length;
    data.TransferData.Buffer = buffer;
    return CfExecute(&req->operation, &data);
}
static HANDLE MetadataHandle(const wchar_t* path) {
    return CreateFileW(path, FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES | FILE_WRITE_DATA,
                       FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                       FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, nullptr);
}
RB_API DWORD RBState(const wchar_t* path) {
    HANDLE file = CreateFileW(path, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    if (file == INVALID_HANDLE_VALUE) return 0;
    FILE_ATTRIBUTE_TAG_INFO attributes{};
    BOOL ok = GetFileInformationByHandleEx(file, FileAttributeTagInfo, &attributes, sizeof(attributes));
    CloseHandle(file);
    return ok ? CfGetPlaceholderStateFromAttributeTag(attributes.FileAttributes, attributes.ReparseTag) : 0;
}
static CF_FS_METADATA Metadata(long long size, long long modified, bool directory) {
    CF_FS_METADATA metadata{};
    metadata.FileSize.QuadPart = directory ? 0 : size;
    metadata.BasicInfo.CreationTime.QuadPart = modified;
    metadata.BasicInfo.LastWriteTime.QuadPart = modified;
    metadata.BasicInfo.ChangeTime.QuadPart = modified;
    metadata.BasicInfo.FileAttributes = directory ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
    return metadata;
}
RB_API HRESULT RBCreate(const wchar_t* parent, const wchar_t* name, const void* identity, DWORD identityLength,
                         long long size, long long modified, BOOL directory) {
    if (!name || !*name || wcschr(name, L'\\') || wcschr(name, L'/') || wcscmp(name, L"..") == 0 || wcscmp(name, L".") == 0)
        return E_INVALIDARG;
    CF_PLACEHOLDER_CREATE_INFO item{};
    item.RelativeFileName = name; item.FsMetadata = Metadata(size, modified, directory != FALSE);
    item.FileIdentity = identity; item.FileIdentityLength = identityLength;
    item.Flags = CF_PLACEHOLDER_CREATE_FLAG_MARK_IN_SYNC;
    HRESULT hr = CfCreatePlaceholders(parent, &item, 1, CF_CREATE_FLAG_NONE, nullptr);
    return FAILED(hr) ? hr : item.Result;
}
RB_API HRESULT RBConvert(const wchar_t* path, const void* identity, DWORD identityLength, BOOL directory, BOOL inSync) {
    HANDLE file = MetadataHandle(path);
    if (file == INVALID_HANDLE_VALUE) return HRESULT_FROM_WIN32(GetLastError());
    CF_CONVERT_FLAGS flags = inSync ? CF_CONVERT_FLAG_MARK_IN_SYNC : CF_CONVERT_FLAG_NONE;
    if (directory) flags |= CF_CONVERT_FLAG_ENABLE_ON_DEMAND_POPULATION;
    HRESULT hr = CfConvertToPlaceholder(file, identity, identityLength, flags, nullptr, nullptr);
    CloseHandle(file); return hr;
}
RB_API HRESULT RBUpdate(const wchar_t* path, const void* identity, DWORD identityLength,
                         long long size, long long modified, BOOL directory, BOOL dehydrate) {
    HANDLE file{};
    HRESULT hr = CfOpenFileWithOplock(path, CF_OPEN_FILE_FLAG_EXCLUSIVE | CF_OPEN_FILE_FLAG_WRITE_ACCESS, &file);
    if (FAILED(hr)) return hr;
    auto metadata = Metadata(size, modified, directory != FALSE);
    CF_UPDATE_FLAGS flags = CF_UPDATE_FLAG_VERIFY_IN_SYNC | CF_UPDATE_FLAG_MARK_IN_SYNC;
    if (dehydrate) flags |= CF_UPDATE_FLAG_DEHYDRATE;
    hr = CfUpdatePlaceholder(file, &metadata, identity, identityLength, nullptr, 0, flags, nullptr, nullptr);
    CfCloseHandle(file); return hr;
}
RB_API HRESULT RBMarkInSync(const wchar_t* path) {
    HANDLE file = MetadataHandle(path);
    if (file == INVALID_HANDLE_VALUE) return HRESULT_FROM_WIN32(GetLastError());
    HRESULT hr = CfSetInSyncState(file, CF_IN_SYNC_STATE_IN_SYNC, CF_SET_IN_SYNC_FLAG_NONE, nullptr);
    CloseHandle(file); return hr;
}
RB_API HRESULT RBSetPin(const wchar_t* path, int state) {
    HANDLE file = MetadataHandle(path);
    if (file == INVALID_HANDLE_VALUE) return HRESULT_FROM_WIN32(GetLastError());
    HRESULT hr = CfSetPinState(file, static_cast<CF_PIN_STATE>(state), CF_SET_PIN_FLAG_NONE, nullptr);
    CloseHandle(file); return hr;
}
RB_API HRESULT RBRegisterShell(const wchar_t* path, const wchar_t* id) {
    HRESULT initialized = RoInitialize(RO_INIT_MULTITHREADED);
    HRESULT result = S_OK;
    try {
        using namespace winrt::Windows::Storage;
        using namespace winrt::Windows::Storage::Provider;
        StorageProviderSyncRootInfo info;
        info.Id(id);
        info.Path(StorageFolder::GetFolderFromPathAsync(path).get());
        info.DisplayNameResource(L"Remote Believe");
        info.IconResource(L"%SystemRoot%\\System32\\imageres.dll,-1040");
        info.Version(L"3.1.0");
        info.HydrationPolicy(StorageProviderHydrationPolicy::Full);
        info.PopulationPolicy(StorageProviderPopulationPolicy::Full);
        info.InSyncPolicy(StorageProviderInSyncPolicy::FileLastWriteTime);
        StorageProviderSyncRootManager::Register(info);
    } catch (...) { result = winrt::to_hresult(); }
    if (SUCCEEDED(initialized)) RoUninitialize();
    return result;
}
RB_API HRESULT RBUnregisterShell(const wchar_t* id) {
    HRESULT initialized = RoInitialize(RO_INIT_MULTITHREADED);
    HRESULT result = S_OK;
    try { winrt::Windows::Storage::Provider::StorageProviderSyncRootManager::Unregister(id); }
    catch (...) { result = winrt::to_hresult(); }
    if (SUCCEEDED(initialized)) RoUninitialize();
    return result;
}
RB_API HRESULT RBHydrate(const wchar_t* path) {
    HANDLE file = CreateFileW(path, FILE_READ_DATA | FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (file == INVALID_HANDLE_VALUE) return HRESULT_FROM_WIN32(GetLastError());
    LARGE_INTEGER offset{}, length{}; length.QuadPart = CF_EOF;
    HRESULT hr = CfHydratePlaceholder(file, offset, length, CF_HYDRATE_FLAG_NONE, nullptr);
    CloseHandle(file); return hr;
}
RB_API HRESULT RBShortcut(const wchar_t* link, const wchar_t* target) {
    HRESULT initialized = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    IShellLinkW* shortcut = nullptr;
    HRESULT hr = CoCreateInstance(CLSID_ShellLink, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&shortcut));
    if (SUCCEEDED(hr)) {
        shortcut->SetPath(target); shortcut->SetWorkingDirectory(target);
        shortcut->SetDescription(L"Carpeta autorizada de Remote Believe");
        IPersistFile* file = nullptr;
        hr = shortcut->QueryInterface(IID_PPV_ARGS(&file));
        if (SUCCEEDED(hr)) { hr = file->Save(link, TRUE); file->Release(); }
        shortcut->Release();
    }
    if (SUCCEEDED(initialized)) CoUninitialize();
    return hr;
}
RB_API void RBShellChanged(const wchar_t* path) {
    SHChangeNotify(SHCNE_UPDATEITEM, SHCNF_PATHW, path, nullptr);
    std::wstring folder(path);
    DWORD attributes = GetFileAttributesW(path);
    if (attributes == INVALID_FILE_ATTRIBUTES || !(attributes & FILE_ATTRIBUTE_DIRECTORY)) {
        auto slash = folder.find_last_of(L"\\/");
        if (slash != std::wstring::npos) folder.resize(slash);
    }
    SHChangeNotify(SHCNE_UPDATEDIR, SHCNF_PATHW, folder.c_str(), nullptr);
}
