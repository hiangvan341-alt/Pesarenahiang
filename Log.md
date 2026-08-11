## V1.2.6 — 05/08/2026 00:47 (GMT+7)
- Sửa lỗi tài khoản vẫn bị báo còn trận chưa hoàn tất dù phòng đã bị đóng hoặc không còn tồn tại.
- Chỉ khóa tạo phòng khi bản ghi trận còn liên kết với một phòng đang hoạt động.
- Bỏ qua các trận mồ côi có trạng thái `playing`/`waiting_confirm` nhưng phòng đã `cancelled` hoặc đã mất.
- Đồng bộ xóa cache trận sau khi ghi nhận bỏ cuộc do chủ phòng Offline.
- File sửa: `app.py`, `modules/forfeit_history_service.py`.

## V1.2.5 — 05/08/2026 00:43 (GMT+7)
- Admin hiển thị riêng các phòng đã tự đóng do chủ phòng Offline.
- Phòng đã đóng không còn khóa người chơi nhưng vẫn lưu để Admin xem chủ, khách, đội, lý do và chi tiết phòng.
- Bổ sung đầy đủ trạng thái phòng đang hoạt động trong tab quản trị.
- File sửa: `app.py`, `modules/admin_dashboard_routes.py`, `templates/admin.html`.

## V1.14.41.58 — 2026-08-02 07:45 (Asia/Bangkok)

- Thêm thưởng RP hoạt động tuần theo số trận và số đối thủ khác nhau.
- Mỗi mốc chỉ nhận một lần/tuần bằng bảng `weekly_rp_rewards`.
- Mốc thưởng cộng dồn: 10 trận +20; 5 đối thủ +30; 10 đối thủ +50; 20 đối thủ +50 RP.
- Chỉ trận confirmed được xét thưởng; tranh chấp chỉ được xét sau khi Admin xác nhận.
- Thêm SQL `docs/update_weekly_rp_rewards_v1_14_41_58.sql`.

## V1.14.41.57 — 2026-08-02 07:17 (Asia/Bangkok)

- Đổi thời gian chờ xác nhận kết quả Rank từ 12 giờ xuống 1 phút.
- Hết 1 phút không xác nhận hoặc tranh chấp, hệ thống tự xác nhận và cộng/trừ RP.
- Luồng phòng và luồng kết quả tiếp tục độc lập: hủy phòng không hủy kết quả đang chờ.
- Trận có tranh chấp không tự xác nhận, chờ Admin xử lý.

## V1.14.41.53 — Bảo vệ Hủy/Xóa phòng Admin — 02/08/2026 01:47 (Asia/Bangkok)
- Khách đã Sẵn sàng vẫn có thể bị chủ phòng đưa ra nếu phòng chưa tạo trận (`waiting_ready`, không có `match_id`); không ảnh hưởng RP.
- Admin Hủy phòng giữ lịch sử phòng/trận, hoàn tác RP trước khi cập nhật trạng thái và hủy lời mời liên kết.
- Admin chỉ được xóa vật lý phòng chờ chưa có trận; phòng có trận bắt buộc dùng Hủy.
- File: `app.py`, `modules/admin_data_routes.py`, `templates/admin.html`.

## V1.14.41.39 — 31/07/2026 11:25 (Asia/Bangkok)


## V1.14.41.51 — Sửa xóa tài khoản làm tụt RP — 02/08/2026 01:32 (Asia/Bangkok)

- Sửa `modules/data_cleanup_service.py`: xóa tài khoản không còn hoàn tác RP/thống kê của các đối thủ từng thi đấu.
- Chặn xử lý trùng một trận khi trận vừa nằm trong phòng vừa nằm trong danh sách trận cache.
- Giữ nguyên hành vi hoàn tác RP khi Admin chủ động xóa phòng/trận riêng lẻ.
- Thêm kiểm tra nguồn `TEST_DELETE_PLAYER_RP_V1.14.41.51.py`.

- Sửa lỗi số phiên bản trên giao diện bị giữ ở `V1.14.41.36`.
- Nguyên nhân: các bản 37 và 38 không cập nhật hằng số `APP_VERSION` trong `app.py`.
- Cập nhật `APP_VERSION` thành `V1.14.41.39`.

## V1.14.41.40
- Rà soát request/polling, dữ liệu trùng và file tải thừa.
- Chỉ tải zcoin_rewards CSS/JS tại endpoint tương ứng.
- Room state dừng khi tab ẩn; pending invite dùng chu kỳ 2,2s/8s.
- Xóa module/template Zcoin cũ không còn dùng.
- Bỏ reload bảo trì 30 giây bị trùng.


## V1.14.41.50 — Tối ưu ảnh — 02/08/2026 01:28 (Asia/Bangkok)
- Rà soát toàn bộ ảnh trong dự án.
- Xóa ảnh local đã có trong `SUPABASE_ASSET_MANIFEST.csv`.
- Xóa PNG cũ/trùng WebP và ảnh kiểm thử không dùng.
- Sửa `static/style.css` để nền đăng nhập chỉ lấy qua `asset_url()`/Supabase.
- Thêm `IMAGE_OPTIMIZATION_V1.14.41.50.md`.


## V1.14.41.52 — Xóa mềm tài khoản và bảo vệ thao tác kích khách — 02/08/2026 01:43 (Asia/Bangkok)

- Đổi xóa tài khoản sang xóa mềm: giữ nguyên dòng `users`, toàn bộ `matches`, phòng đã có `match_id`, tỷ số và RP lịch sử.
- Vô hiệu hóa đăng nhập bằng `account_status=banned`, đặt mật khẩu ngẫu nhiên và trạng thái Offline.
- Chỉ dọn phòng chờ chưa có trận, thiết bị đăng nhập và lời mời chưa hoàn tất.
- Sửa nút Admin thành “Xóa mềm” và cảnh báo rõ lịch sử/RP được giữ nguyên.
- Rà cơ chế chủ phòng kích khách: chỉ cho phép trước khi bắt đầu; chặn thêm khi đã có `match_id`.
- Khi kích khách, đóng lời mời liên kết để không còn trạng thái lời mời treo; không xóa trận và không thay đổi RP.

## V1.14.41.54 — 02/08/2026 01:53 (Asia/Bangkok)
- Bỏ hoàn toàn chức năng Admin xóa phòng; giao diện chỉ còn nút **Hủy phòng**.
- Hủy phòng chỉ giải phóng người chơi để tạo phòng mới, không hoàn tác hoặc thay đổi RP.
- Hỗ trợ phòng một người, chưa có trận, đang chơi, đã có kết quả, chờ xác nhận, tranh chấp và có báo cáo.
- Giữ nguyên lịch sử, tỷ số, delta RP, báo cáo và bằng chứng tranh chấp.
- Trận chưa hoàn tất chuyển `cancelled` để không khóa người chơi; trận đã `confirmed` giữ nguyên.
- File: `app.py`, `modules/admin_data_routes.py`, `templates/admin.html`.

## V1.14.41.55 - 02/08/2026
- Tách trạng thái tranh chấp khỏi trạng thái phòng.
- Trận bị tranh chấp vẫn lưu và chưa tính RP; phòng lập tức trở lại Chờ Sẵn Sàng.
- Người chơi có thể tiếp tục thi đấu trong cùng phòng mà không chờ Admin xử lý tranh chấp cũ.
- File: `modules/room_result_routes.py`, `app.py`.


## V1.14.41.56 — 2026-08-02 07:12 (Asia/Bangkok)
- Tách hủy phòng khỏi xử lý kết quả.
- Tự xác nhận trận chờ sau 12 giờ, không phạt người quên xác nhận.
- Khóa xác nhận trực tiếp trận disputed.

## V1.14.41.59 — 02/08/2026 08:08 (UTC+7)
- Điều chỉnh mốc thưởng tuần mặc định thành 20 + 30 + 50 + 20 = tối đa 120 RP.
- Bổ sung cấu hình thưởng tuần trong Admin > Hệ thống.
- File sửa: `modules/weekly_rp_rewards_service.py`, `modules/admin_system_routes.py`, `templates/admin.html`, `app.py`.

## V1.14.41.60 - 2026-08-02
- Sửa animation Win Streak và SHUTDOWN không xuất hiện khi trận được tự xác nhận sau 1 phút.
- File: app.py, UPDATE_MANIFEST_V1.14.41.60.md.

## V1.14.41.62 — 02/08/2026 09:24 (Asia/Bangkok)
- Sửa Remember this account: dùng phiên đăng nhập 30 ngày và Password Manager của trình duyệt.
- Tài khoản Admin tạo/import được dùng mật khẩu 1 ký tự.
- Tài khoản Admin tạo/import bỏ giới hạn thiết bị và cảnh báo trùng IP, nhưng vẫn tính RP bình thường.
- File: `app.py`, `modules/admin_account_routes.py`, `templates/admin.html`, `templates/login.html`.

## V1.14.41.65 — 2026-08-02 18:19 (Asia/Bangkok)
- Hoàn thiện bảo vệ phiên: truy vấn trực tiếp phòng theo user và trạng thái cần bảo vệ, không phụ thuộc cache `list_rooms()`.
- Đồng nhất trạng thái `playing`, `friendly_playing`, `waiting_result_confirm`, `waiting_confirm`, `disputed`.
- Không đăng xuất khi một phía vừa mất kết nối nhưng phòng vẫn cần hoàn tất.
- Admin hiển thị trạng thái tải `user_devices`, số bản ghi, số tài khoản có IP, số nhóm trùng và nút tải lại.
- Đổi nhãn Remember thành “Ghi nhớ đăng nhập trên thiết bị này”; làm rõ mật khẩu do trình duyệt lưu.
- Cập nhật kiểm thử: 94/94 đạt.
- File chính: `app.py`, `modules/session_runtime_service.py`, `modules/admin_dashboard_routes.py`, `templates/admin.html`, `templates/login.html`.

## V1.14.41.66 — 2026-08-02 19:30 (Asia/Bangkok)

- Sửa lỗi khách đã vào phòng nhưng phía chủ phòng không nhìn thấy.
- Bổ sung `host_user_id` và `guest_user_id` vào khóa trạng thái phòng để API phát hiện thay đổi thành viên và frontend tự tải lại phần phòng đấu.
- Không tạo thêm polling hoặc request nền.
- File: `app.py`, `test_room_guest_visibility_v1144166.py`, các test phiên bản, `UPDATE_MANIFEST_V1.14.41.66.md`.

## V1.14.41.67 — 02/08/2026 22:16 (GMT+7)

- Kiểm tra giới hạn trận Rank theo ngày Việt Nam: Thứ Hai–Thứ Sáu 10 trận, Thứ Bảy–Chủ Nhật 15 trận; đổi mốc chính xác lúc 00:00 GMT+7.
- Sửa `active_room_for_user()` truy vấn nhầm bảng `rooms`; nay truy vấn trực tiếp `match_rooms`.
- Bổ sung `waiting_ready` vào nhóm phòng active để người đang có phòng chờ không thể tạo thêm phòng mới.
- Chống double-click và request đồng thời trên nhiều Vercel instance: sau khi tạo phòng sẽ đối chiếu lại và chỉ giữ một phòng hợp lệ.
- Tự dọn các phòng `waiting_ready` trùng, chỉ xóa phòng chưa có `match_id`; không ảnh hưởng trận đang đá, kết quả, RP hoặc tranh chấp.
- Khi Admin mở trang quản trị, hệ thống tự dọn các phòng chờ trùng cũ và tải lại danh sách.
- Hủy lời mời pending gắn với phòng trùng đã bị xóa để tránh trạng thái lời mời treo.
- Kiểm tra tự động: 101/101 test đạt.

### File thay đổi
- `app.py`
- `modules/admin_dashboard_routes.py`
- `test_v1144167_room_daily_limit.py`
- `Log.md`


## V1.14.41.68 — 02/08/2026 23:35 (GMT+7)
- Sửa công thức thưởng chuỗi: chỉ RP thắng cơ bản chịu hệ số gặp lại và hệ số chủ phòng.
- Thưởng chuỗi được cộng nguyên vẹn.
- Đồng bộ luồng xác nhận trận và tính lại BXH Admin.
- Thêm test riêng cho thắng lần 3 cùng đối thủ khi chạm chuỗi 10.

## V1.14.41.73–77 — Profile V2
- Làm mới trang hồ sơ theo bố cục Champion Showcase / Arena Overview.
- Banner phủ khung, có lớp gradient; avatar, RP, Rank, huy hiệu và hành trình Rank rõ hơn.
- Hồ sơ chưa trang bị banner không còn hiện cụm chữ lớn mặc định.
- Không thay đổi SQL hoặc logic thi đấu.

## V1.14.41.78 — Room Session Guard
- Bảo vệ phòng đang thi đấu tối đa 4 giờ khi người chơi chuyển sang PES/Parsec.
- Request trang/API phòng được tính là hoạt động trước bộ lọc idle.
- Tab nền tiếp tục đồng bộ phiên; người ngoài phòng vẫn timeout sau 60 phút.

## V1.14.41.79 — Result Confirmation Reliability
- Sửa lỗi `NameError: get_win_streak_bonus is not defined` khi khách xác nhận tỷ số.
- `match_result_service.py` import trực tiếp `random` và `get_win_streak_bonus`.
- Giữ nguyên công thức RP, giới hạn ngày, hệ số gặp lại và session guard V1.14.41.78.

## V1.14.41.79 Clean — 04/08/2026 01:42 (Asia/Bangkok)
- Xóa toàn bộ Markdown thừa, chỉ giữ `Log.md`.
- Xóa cache Python/Pytest và các manifest TXT cũ.
- Xóa ảnh local đã có trong `SUPABASE_ASSET_MANIFEST.csv` cùng PNG/test image trùng hoặc không dùng.
- ZIP không bọc thư mục cha; yêu cầu cấu hình `STATIC_ASSET_BASE_URL` và `SHOP_ASSET_BASE_URL` trên Vercel.


## V1.14.41.80 — 04/08/2026 01:55 (GMT+7)
- Hòa đặt chuỗi thắng về 0; đồng bộ cả luồng xác nhận trực tiếp và tính lại BXH Admin.
- Đối thủ bỏ cuộc: người còn lại được +1 trận thắng và +1 chuỗi thắng, nhưng +0 RP.
- Giữ tự động xác nhận sau 60 giây và hiển thị đồng hồ đếm ngược ngay dưới tỷ số.
- File sửa: `app.py`, `modules/match_result_service.py`, `modules/admin_ranking_rebuild.py`, `modules/room_rematch_routes.py`, 3 template phòng, `static/style.css`.


## V1.2.0 — 04/08/2026 02:00 (GMT+7)

- Nâng phiên bản chính lên V1.2.0.
- Kiểm tra và gia cố toàn bộ luồng nhập/xác nhận tỷ số.
- Không cho polling thay khung phòng khi chủ phòng đang nhập tỷ số.
- Kiểm tra tỷ số 0–99 ở cả trình duyệt và máy chủ; không tự đổi ô trống thành 0.
- Giữ bản nháp tỷ số khi lỗi mạng.
- Chống trạng thái dở dang khi match đã lưu nhưng phòng chưa đổi trạng thái; tự hoàn tác an toàn.
- Mỗi lỗi lưu/xác nhận có mã riêng SCORE/CONFIRM/ROOM để tra log.
- Phân biệt rõ trường hợp RP đã ghi nhận nhưng phòng chưa làm mới.
- Lỗi phụ của animation chuỗi thắng không còn chặn xác nhận kết quả.

## V1.2.1 — 04/08/2026 02:26 (GMT+7)
- Tự động tạo fingerprint theo nội dung cho CSS/JS, không còn phụ thuộc hoàn toàn vào việc đổi phiên bản để phá cache.
- Tách CSS Thưởng RP tuần thành module riêng, giới hạn phạm vi trong trang Admin và loại bỏ CSS trùng/inline của module này.
- Thêm công cụ `scripts/bump_version.py` và `scripts/check_ui_assets.py` để kiểm tra trước khi đóng gói.
## V1.2.4
- Khi chủ phòng đóng tab/trình duyệt trong trạng thái đang thi đấu, hệ thống xác nhận Offline qua presence rồi tự đóng phòng.
- Chủ phòng bị tính bỏ trận, trừ 20 RP, cộng 1 trận thua và reset chuỗi thắng.
- Khách không thay đổi RP, thống kê hoặc chuỗi; được giải phóng để tạo phòng mới.
- Giữ nguyên quyền Admin hủy phòng mà không phạt thêm người chơi.


## V1.2.7 - Fix lời mời không hiển thị
- Lời mời được kiểm tra trên mọi trang đã đăng nhập, kể cả Lịch sử và Hướng dẫn.
- Tab nền vẫn kiểm tra lời mời theo chu kỳ 10 giây.
- API đọc tối đa 20 lời mời pending để không bỏ sót lời mời hợp lệ cũ hơn.
- Lỗi truy vấn API không còn bị hiểu nhầm là không có lời mời.
- Đồng bộ cache lời mời sau khi gửi.

## V1.2.9
- Sửa lỗi người nhận đang ở trang phòng một mình không thấy lời mời.
- Polling và watchdog lời mời tiếp tục chạy trên trang `/room/...`.
- Không thay đổi điều kiện backend: phòng đủ hai người hoặc đã thi đấu vẫn không nhận lời mời mới.
- Kiểm tra hồi quy toàn bộ: 166/166 test đạt.
