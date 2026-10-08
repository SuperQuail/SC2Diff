// Independent container verification using StormLib -- the library the SC2 editor embeds.
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include "StormLib.h"

static void mkdirs(const char* path)
{
    char buf[MAX_PATH * 2];
    strncpy(buf, path, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = 0;
    char* last = NULL;
    for (char* p = buf; *p; p++) if (*p == '\\' || *p == '/') last = p;
    if (last == NULL) return;
    *last = 0;
    for (char* p = buf; *p; p++)
    {
        if (*p == '\\' || *p == '/')
        {
            char c = *p; *p = 0;
            if (buf[0]) CreateDirectoryA(buf, NULL);
            *p = c;
        }
    }
    if (buf[0]) CreateDirectoryA(buf, NULL);
}

int main(int argc, char** argv)
{
    if (argc < 3) { printf("usage: stormlib_verify <archive> <outdir>\n"); return 2; }
    HANDLE hMpq = NULL;
    if (!SFileOpenArchive(argv[1], 0, MPQ_OPEN_READ_ONLY, &hMpq))
    {
        printf("RESULT SFileOpenArchive FAILED code=%u\n", GetLastError());
        return 1;
    }
    printf("RESULT SFileOpenArchive OK\n");

    DWORD flags = 0;
    if (SFileGetFileInfo(hMpq, SFileMpqFlags, &flags, sizeof(flags), NULL))
        printf("INFO flags=0x%08X\n", flags);
    ULONGLONG archSize = 0;
    if (SFileGetFileInfo(hMpq, SFileMpqArchiveSize64, &archSize, sizeof(archSize), NULL))
        printf("INFO archiveSize=%llu\n", archSize);
    DWORD hashEntries = 0, blockEntries = 0, hetBytes = 0;
    SFileGetFileInfo(hMpq, SFileMpqHashTableSize, &hashEntries, sizeof(hashEntries), NULL);
    SFileGetFileInfo(hMpq, SFileMpqBlockTableSize, &blockEntries, sizeof(blockEntries), NULL);
    SFileGetFileInfo(hMpq, SFileMpqHetTableSize, &hetBytes, sizeof(hetBytes), NULL);
    printf("INFO hashTableEntries=%u blockTableEntries=%u hetBytes=%u\n", hashEntries, blockEntries, hetBytes);

    SFILE_FIND_DATA fd;
    HANDLE hFind = SFileFindFirstFile(hMpq, "*", &fd, NULL);
    int count = 0, extracted = 0, failed = 0;
    if (hFind == NULL)
    {
        printf("RESULT SFileFindFirstFile FAILED code=%u\n", GetLastError());
    }
    else
    {
        do
        {
            count++;
            HANDLE hFile = NULL;
            if (!SFileOpenFileEx(hMpq, fd.cFileName, 0, &hFile))
            {
                printf("FILE %s OPEN_FAILED %u\n", fd.cFileName, GetLastError());
                failed++;
                continue;
            }
            DWORD sizeHigh = 0;
            DWORD size = SFileGetFileSize(hFile, &sizeHigh);
            char out[MAX_PATH * 2];
            char name[MAX_PATH];
            strncpy(name, fd.cFileName, sizeof(name) - 1);
            name[sizeof(name) - 1] = 0;
            for (char* p = name; *p; p++) if (*p == '/') *p = '\\';
            _snprintf(out, sizeof(out) - 1, "%s\\%s", argv[2], name);
            out[sizeof(out) - 1] = 0;
            mkdirs(out);

            HANDLE hOut = CreateFileA(out, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
            static char buf[262144];
            DWORD total = 0, read = 0;
            BOOL ok = TRUE;
            while (ok && total < size)
            {
                DWORD want = (size - total) > sizeof(buf) ? (DWORD)sizeof(buf) : (DWORD)(size - total);
                if (!SFileReadFile(hFile, buf, want, &read, NULL)) { ok = FALSE; break; }
                if (read == 0) break;
                DWORD written = 0;
                if (!WriteFile(hOut, buf, read, &written, NULL) || written != read) { ok = FALSE; break; }
                total += read;
            }
            if (hOut != INVALID_HANDLE_VALUE) CloseHandle(hOut);
            SFileCloseFile(hFile);
            if (ok && total == size) extracted++;
            else { failed++; printf("FILE %s READ_FAILED code=%u got=%u want=%u\n", fd.cFileName, GetLastError(), total, size); }
        } while (SFileFindNextFile(hFind, &fd));
        SFileFindClose(hFind);
    }
    printf("RESULT enumerated=%d extracted=%d failed=%d\n", count, extracted, failed);
    SFileCloseArchive(hMpq);
    return failed == 0 ? 0 : 1;
}
